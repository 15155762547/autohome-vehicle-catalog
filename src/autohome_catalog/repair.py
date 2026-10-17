"""Repair only failed manifest members using the website's spec-page endpoint."""

import json
from urllib.parse import parse_qs, urlencode, urlparse

import scrapy

from .models import ParameterResponse, SpecList
from .parse import parse_parameters
from .spider import BASE, CatalogSpider
from .storage import save_json


def merge_parts(parts, series_id):
    """Remap response-local title IDs by source identity, without changing cell content."""
    groups, titles, specs = {}, {}, {}
    bread = None
    for part in parts:
        payload, selected = part["payload"], set(part["spec_ids"])
        parse_parameters(payload, series_id, selected)
        bread = bread or payload["bread"]
        local = {}
        for group in payload["titlelist"]:
            section = (group["groupname"], group["itemtype"])
            target = groups.setdefault(section, {**group, "items": []})
            for item in group["items"]:
                key = (*section, item["itemid"], item["itemname"])
                if key not in titles:
                    titles[key] = len(titles) + 1
                    target["items"].append({**item, "titleid": titles[key]})
                local[item["titleid"]] = titles[key]
        for spec in payload["datalist"]:
            if spec["specid"] not in selected:
                continue
            if spec["specid"] in specs:
                raise ValueError("Overlapping selected configurations in parameter parts")
            specs[spec["specid"]] = {
                **spec,
                "paramconflist": [
                    {**c, "titleid": local[c["titleid"]]} for c in spec["paramconflist"]
                ],
            }
    if not bread:
        raise ValueError("No valid parameter evidence")
    return {"bread": bread, "titlelist": list(groups.values()), "datalist": list(specs.values())}


def sources_by_spec(packet):
    if "parameter_parts" not in packet:
        return {s["id"]: packet["parameter_source"] for s in packet["spec_list"]}
    return {sid: part["source"] for part in packet["parameter_parts"] for sid in part["spec_ids"]}


class RepairSpider(CatalogSpider):
    name = "repair"

    def __init__(self, root, **kwargs):
        super().__init__(root, **kwargs)
        status = json.loads((self.root / "status.json").read_text())
        if status["phase"] == "complete" or not status["missing_series"]:
            raise ValueError("No failed manifest members to repair")
        save_json(self.root / "status-first-pass.json", status)
        self.completed = set(status["completed_series"])
        self.directory = json.loads((self.root / "directory.json").read_text())["rows"]
        self.catalog_rows = self.directory
        self.missing = set(status["missing_series"])
        self.pending, self.contexts, self.parts = {}, {}, {}
        self.receipts = {}
        for path in (self.root / "sources").glob("*.json"):
            source = json.loads(path.read_text())
            query = parse_qs(urlparse(source["url"]).query)
            sid = query.get("seriesid", [""])[0]
            if sid and int(sid) in self.missing:
                kind = "parameters" if "/param/getParamConf?" in source["url"] else "spec_list"
                self.receipts[(int(sid), kind)] = source

    async def start(self):
        for series in self.directory:
            sid = series["seriesid"]
            if sid not in self.missing:
                continue
            spec_source = self.receipts[(sid, "spec_list")]
            listing = json.loads((self.root / spec_source["file"]).read_bytes())["result"]
            items = SpecList.model_validate(listing).items
            wanted = set(series["specids"])
            selected = [s.model_dump() for s in items if s.id in wanted]
            if {s["id"] for s in selected} != wanted:
                raise ValueError("Repair spec scope differs from the original manifest")
            self.contexts[sid] = {
                "series": series,
                "spec_list": selected,
                "spec_list_source": spec_source,
            }
            self.parts[sid] = []
            source = self.receipts[(sid, "parameters")]
            payload = json.loads((self.root / source["file"]).read_bytes())["result"]
            present = wanted & {s["specid"] for s in payload.get("datalist", [])}
            if present:
                parse_parameters(payload, sid, present)
                self.parts[sid].append(
                    {"source": source, "payload": payload, "spec_ids": sorted(present)}
                )
            self.pending[sid] = wanted - present
            for spec_id in sorted(self.pending[sid]):
                query = urlencode({"mode": 1, "site": 2, "specid": spec_id})
                yield scrapy.Request(
                    BASE + "param/getParamConf?" + query,
                    callback=self.parse_repair,
                    errback=self.failed,
                    cb_kwargs={"series_id": sid, "spec_id": spec_id},
                )

    def parse_repair(self, response, series_id, spec_id):
        payload = self.source_json(response)
        parse_parameters(payload, series_id, {spec_id})
        parsed = ParameterResponse.model_validate(payload)
        expected = {s["id"]: s["specstate"] for s in self.contexts[series_id]["spec_list"]}
        actual = next(s for s in parsed.datalist if s.specid == spec_id)
        if actual.specstatus != expected[spec_id]:
            raise ValueError("Configuration state changed during repair")
        self.parts[series_id].append(
            {"source": response.meta["source_receipt"], "payload": payload, "spec_ids": [spec_id]}
        )
        self.pending[series_id].remove(spec_id)
        if not self.pending[series_id]:
            packet = {
                **self.contexts[series_id],
                "parameter_parts": self.parts[series_id],
                "parameter_source": self.parts[series_id][0]["source"],
                "payload": merge_parts(self.parts[series_id], series_id),
            }
            all_ids = {s["id"] for s in packet["spec_list"]}
            parse_parameters(packet["payload"], series_id, all_ids)
            save_json(self.root / f"series/{series_id}.json", packet)
            self.completed.add(series_id)
            self.report_progress(series_id)
