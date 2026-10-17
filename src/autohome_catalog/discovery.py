"""Snapshot the two explicitly authorized NEV search tabs with page evidence."""

from math import ceil
from urllib.parse import parse_qs, urlencode, urlparse

import scrapy
from pydantic import BaseModel, ConfigDict

from .spider import BASE, CatalogSpider
from .storage import save_json


class SearchSeries(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")
    seriesid: int
    seriesname: str
    seriesstate: int
    specids: list[int]
    specnum: int


class DiscoverySpider(CatalogSpider):
    name = "discovery"

    def __init__(self, root, expected_on_sale, expected_presale, **kwargs):
        super().__init__(root, brands=(), **kwargs)
        self.expected_counts = {2: expected_on_sale, 3: expected_presale}
        self.pages = {2: {}, 3: {}}

    async def start(self):
        for state in self.expected_counts:
            yield self.page_request(state, 1)

    def page_request(self, state, page):
        query = urlencode(
            {"searchtype": 6, "pageindex": page, "pagesize": 20, "orderid": 0, "state": state}
        )
        return scrapy.Request(
            BASE + "search/searchcar?" + query,
            callback=self.parse_search,
            errback=self.failed,
            cb_kwargs={"state": state, "page": page},
        )

    def parse_search(self, response, state, page):
        result = self.source_json(response)
        total = result["seriescount"]
        if total != self.expected_counts[state]:
            raise ValueError(f"Source count changed for state {state}: {total}")
        rows = [SearchSeries.model_validate(row).model_dump() for row in result["seriesgrouplist"]]
        if len(rows) != min(20, total - (page - 1) * 20):
            raise ValueError(f"Incomplete search page: {state}/{page}")
        self.pages[state][page] = {"rows": rows, "source": response.meta["source_receipt"]}
        if page == 1:
            for number in range(2, ceil(total / 20) + 1):
                yield self.page_request(state, number)

    def closed_report(self, spider, reason):
        rows, counts = [], {}
        for state, expected in self.expected_counts.items():
            members = []
            for page, packet in sorted(self.pages[state].items()):
                for row in packet["rows"]:
                    members.append(row["seriesid"])
                    rows.append(
                        {
                            **row,
                            "scope_status": "在售" if state == 2 else "预售",
                            "scope_basis": "汽车之家找新能源→"
                            + ("在售" if state == 2 else "预售")
                            + "目录；公司要求采集在售及预售",
                            "catalog_source": packet["source"],
                            "catalog_state": state,
                            "catalog_page": page,
                        }
                    )
            counts[str(state)] = len(members)
            if len(members) != expected or len(set(members)) != len(members):
                self.errors.append({"state": state, "reason": "Missing or duplicate series"})
        stats = self.crawler.stats.get_stats()
        exceptions = any(k.startswith("spider_exceptions/") and v for k, v in stats.items())
        complete = reason == "finished" and not self.errors and not exceptions
        membership = {
            state: {r["seriesid"] for r in rows if r["catalog_state"] == state} for state in (2, 3)
        }
        result = {
            "phase": "complete" if complete else "complete_with_issues",
            "source_page": "https://www.autohome.com.cn/price/ev",
            "scope_rule": "公司要求：找新能源目录的在售和预售；排除停售及其他未售",
            "counts": counts,
            "overlapping_series": sorted(membership[2] & membership[3]),
            "rows": rows,
            "errors": self.errors,
        }
        save_json(self.root / "manifest.json", result)
        save_json(self.root / "status.json", {k: v for k, v in result.items() if k != "rows"})


def load_manifest(path):
    import hashlib
    import json

    from .storage import assert_external

    path = assert_external(path)
    manifest = json.loads(path.read_text())
    if manifest["phase"] != "complete" or manifest["errors"] or manifest["overlapping_series"]:
        raise ValueError("Directory manifest is incomplete or ambiguous")
    rows = manifest["rows"]
    for state in (2, 3):
        chosen = [r for r in rows if r["catalog_state"] == state]
        if len(chosen) != manifest["counts"][str(state)]:
            raise ValueError("Manifest count differs from rows")
    if len({r["seriesid"] for r in rows}) != len(rows):
        raise ValueError("Duplicate manifest series IDs")
    for row in rows:
        SearchSeries.model_validate(row)
        state = row["catalog_state"]
        if state not in (2, 3) or row["scope_status"] != {2: "在售", 3: "预售"}[state]:
            raise ValueError("Unexpected manifest sales status")
        if not row["specids"] or not (
            len(row["specids"]) == len(set(row["specids"])) == row["specnum"]
        ):
            raise ValueError("Invalid directory configuration identities")
        source = row["catalog_source"]
        query = parse_qs(urlparse(source["url"]).query)
        if query.get("state") != [str(state)] or query.get("searchtype") != ["6"]:
            raise ValueError("Receipt is not from the requested NEV sales tab")
        raw_path = (path.parent / source["file"]).resolve()
        if not raw_path.is_relative_to(path.parent.resolve()):
            raise ValueError("Source path is outside the directory snapshot")
        raw = raw_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != source["sha256"]:
            raise ValueError("Directory source hash mismatch")
        data = json.loads(raw)["result"]
        original = next(
            (r for r in data["seriesgrouplist"] if r["seriesid"] == row["seriesid"]), None
        )
        if original is None or any(
            original[k] != row[k]
            for k in ("seriesid", "seriesname", "seriesstate", "specids", "specnum")
        ):
            raise ValueError("Series is not in its captured directory source")
    return manifest
