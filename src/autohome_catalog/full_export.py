"""Complete text CSV exports independent of Feishu's per-table column budget."""

import csv
import json
from collections import Counter
from pathlib import Path

from .export import POWERTRAIN_CODES, key_of, load_dictionary, verify_packet_sources
from .parse import cell_text, parse_parameters, render_cell
from .repair import sources_by_spec
from .storage import assert_external, save_json


def merge_order(order, sequence):
    for index, key in enumerate(sequence):
        if key not in order:
            following = next((k for k in sequence[index + 1 :] if k in order), None)
            order.insert(order.index(following) if following is not None else len(order), key)


def export_full(root: Path):
    root = assert_external(root)
    status = json.loads((root / "status.json").read_text())
    manifest = json.loads((root / "scope-manifest.json").read_text())
    expected = {r["seriesid"] for r in manifest["rows"]}
    files = {int(p.stem): p for p in (root / "series").glob("*.json")}
    if (
        status["phase"] != "complete"
        or set(files) != expected
        or set(status["completed_series"]) != expected
        or status["missing_series"]
    ):
        raise ValueError("Full collection is incomplete; inspect status.json")
    known = {key_of(f): f for f in load_dictionary()}
    sections, keys, fields = [], {}, {}
    # Directory order anchors the merged source order at the screenshot's Seagull entry.
    for member in manifest["rows"]:
        packet = json.loads(files[member["seriesid"]].read_text())
        group_order = []
        for group in packet["payload"]["titlelist"]:
            section = (group["groupname"], group["itemtype"])
            group_order.append(section)
            sequence = []
            for item in group["items"]:
                key = (*section, item["itemid"], item["itemname"])
                fields.setdefault(
                    key,
                    {
                        "group": key[0],
                        "section": key[1],
                        "item_id": key[2],
                        "name": key[3],
                        "known": key in known,
                    },
                )
                sequence.append(key)
            merge_order(keys.setdefault(section, []), sequence)
        merge_order(sections, group_order)
    ordered = [k for section in sections for k in keys[section]]
    names = {k: known[k]["column"] for k in ordered if k in known}
    used = set(names.values())
    for key in ordered:
        if key not in names:
            name = key[3].replace("[", "（").replace("]", "）")
            if name in used:
                name += f"({key[1]}·{key[2]})"
            if name in used:
                name += f"({key[0]})"
            if name in used:
                raise ValueError(f"Ambiguous display column: {key}")
            names[key] = name
            used.add(name)
        fields[key]["column"] = names[key]
    save_json(root / "full-field-catalog.json", [fields[k] for k in ordered])
    metadata = ["车系名称", "model_id", "配置名称", "销售状态", "来源车系ID", "来源配置ID"]
    provenance = ["来源参数页", "来源采集URL", "来源采集时间", "来源快照SHA256", "原始响应文件"]
    headers = metadata + [names[k] for k in ordered] + provenance
    if len(headers) != len(set(headers)):
        raise ValueError("Duplicate CSV headers")
    seen_sources, seen_specs = set(), set()
    models, condition_count, cells_count = [], 0, 0
    energies, unresolved_ids = Counter(), []
    with (root / "完整配置.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        for member in manifest["rows"]:
            sid = member["seriesid"]
            packet = json.loads(files[sid].read_text())
            if packet["series"]["seriesid"] != sid:
                raise ValueError("Packet identity mismatch")
            verify_packet_sources(root, packet)
            packet_sources = sources_by_spec(packet)
            seen_sources.update(p["file"] for p in packet_sources.values())
            seen_sources.update(
                [packet["spec_list_source"]["file"], packet["series"]["catalog_source"]["file"]]
            )
            wanted = set(member["specids"])
            specs = {s["id"]: s for s in packet["spec_list"]}
            if set(specs) != wanted or seen_specs & wanted:
                raise ValueError("Missing, extra or duplicate configurations")
            seen_specs.update(wanted)
            cells = parse_parameters(packet["payload"], sid, wanted)
            model_ids = set()
            for spec_id, values in cells.items():
                source = packet_sources[spec_id]
                energy = values.get(("参数信息", "基本参数", 55, "能源类型"))
                energy_text = cell_text(energy) if energy else "NOT_RETURNED"
                energies[energy_text] += 1
                code = POWERTRAIN_CODES.get(energy_text)
                model_id = f"AUT-{sid}-{code}" if code else ""
                if model_id:
                    model_ids.add(model_id)
                else:
                    unresolved_ids.append(
                        {"series_id": sid, "spec_id": spec_id, "energy": energy_text}
                    )
                row = {
                    "车系名称": packet["payload"]["bread"]["seriesname"],
                    "model_id": model_id,
                    "配置名称": specs[spec_id]["specname"],
                    "销售状态": member["scope_status"],
                    "来源车系ID": sid,
                    "来源配置ID": spec_id,
                    "来源参数页": f"https://www.autohome.com.cn/config/spec/{spec_id}.html",
                    "来源采集URL": source["url"],
                    "来源采集时间": source["fetched_at"],
                    "来源快照SHA256": source["sha256"],
                    "原始响应文件": source["file"],
                }
                for key, value in values.items():
                    row[names[key]] = render_cell(value)
                    cells_count += 1
                    if key in known and known[key]["kind"] == "number":
                        from .parse import numeric_cell

                        if numeric_cell(value) is None and cell_text(value) not in (
                            "",
                            "-",
                            "—",
                            "暂无数据",
                            "暂无报价",
                        ):
                            condition_count += 1
                writer.writerow(row)
            models.append(
                {
                    "车系名称": packet["payload"]["bread"]["seriesname"],
                    "model_id": "\n".join(sorted(model_ids)),
                    "品牌": packet["payload"]["bread"]["brandname"],
                    "销售状态": member["scope_status"],
                    "来源车系ID": sid,
                    "配置数": len(wanted),
                    "来源车系页": f"https://www.autohome.com.cn/{sid}/",
                }
            )
    with (root / "完整车型.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(models[0]))
        writer.writeheader()
        writer.writerows(models)
    with (root / "字段目录.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fields[ordered[0]]))
        writer.writeheader()
        writer.writerows(fields[k] for k in ordered)
    result = {
        "models": len(models),
        "trims": len(seen_specs),
        "parameter_fields": len(ordered),
        "new_fields": len(set(fields) - set(known)),
        "source_cells": cells_count,
        "verified_source_files": len(seen_sources),
        "numeric_conditions_preserved_as_text": condition_count,
        "sales_counts": manifest["counts"],
        "missing_series": [],
        "missing_specs": [],
    }
    result["energy_types"] = dict(energies)
    result["unresolved_model_ids"] = unresolved_ids
    save_json(root / "full-export-verification.json", result)
    return result
