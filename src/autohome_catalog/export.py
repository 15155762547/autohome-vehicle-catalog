"""Chinese independent columns plus exact configuration/source evidence."""

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from .parse import cell_text, numeric_cell, parse_parameters, render_cell
from .storage import assert_external, save_json

MODEL_FIELDS = [
    "车系名称",
    "来源车系ID",
    "品牌",
    "来源车系页",
    "来源采集时间",
    "来源快照SHA256",
    "车系图片URL",
    "图片来源页",
]
TRIM_FIELDS = [
    "配置名称",
    "来源配置ID",
    "来源车系ID",
    "销售状态",
    "销售状态依据",
    "原始销售状态码",
    "来源参数页",
    "来源采集URL",
    "来源采集时间",
    "来源快照SHA256",
    "图片来源URL清单",
]
EQUIPMENT_FIELDS = ["配置名称", "来源配置ID", "来源参数页", "来源采集时间", "来源快照SHA256"]


def key_of(field):
    return field["group"], field["section"], field["item_id"], field["name"]


def raw_column(field):
    return "原文｜" + field["column"]


def load_dictionary(path: Path | None = None):
    if path is None:
        path = Path(__file__).resolve().parents[2] / "schema/fields.json"
        if not path.exists():
            path = Path(__file__).parent / "fields.json"
    fields = json.loads(path.read_text())["fields"]
    assert_dictionary(fields)
    return fields


def assert_dictionary(fields):
    if len({key_of(f) for f in fields}) != len(fields):
        raise ValueError("Duplicate source field keys")
    if len({f["column"] for f in fields}) != len(fields):
        raise ValueError("Duplicate output column names")
    counts = Counter(f["table"] for f in fields)
    counts.update(f["table"] for f in fields if f["kind"] == "number")
    all_columns = [f["column"] for f in fields] + [
        raw_column(f) for f in fields if f["kind"] == "number"
    ]
    if len(all_columns) != len(set(all_columns)):
        raise ValueError("Duplicate numeric raw/output column names")
    if any(table not in {"trims", "equipment"} for table in counts):
        raise ValueError("Only configuration tables can contain parameters")
    if (
        counts["trims"] + len(TRIM_FIELDS) + 2 > 300
        or counts["equipment"] + len(EQUIPMENT_FIELDS) + 1 > 300
    ):
        raise ValueError("Field dictionary exceeds the 300-column table budget")


def expand_packet(packet: dict, dictionary: list[dict]):
    series, source = packet["series"], packet["parameter_source"]
    source_time = datetime.fromisoformat(source["fetched_at"].replace("Z", "+00:00")).isoformat(
        timespec="milliseconds"
    )
    sid = series["seriesid"]
    spec_list = {s["id"]: s for s in packet["spec_list"]}
    cells_by_spec = parse_parameters(packet["payload"], sid, set(spec_list))
    bread = packet["payload"]["bread"]
    model = {
        "key": str(sid),
        "fields": {
            "车系名称": bread["seriesname"],
            "来源车系ID": str(sid),
            "品牌": bread["brandname"],
            "来源车系页": f"https://www.autohome.com.cn/{sid}/",
            "来源采集时间": source_time,
            "来源快照SHA256": source["sha256"],
            "车系图片URL": series.get("seriesimg") or None,
            "图片来源页": series.get("catalog_source", {}).get("url"),
        },
    }
    result = {"models": [model], "trims": [], "equipment": [], "evidence": []}
    fields_by_key = {key_of(f): f for f in dictionary}
    issues = []
    for spec_id, cells in cells_by_spec.items():
        spec = spec_list[spec_id]
        meta = {
            "配置名称": spec["specname"],
            "来源配置ID": str(spec_id),
            "来源参数页": f"https://www.autohome.com.cn/config/spec/{spec_id}.html",
            "来源采集时间": source_time,
            "来源快照SHA256": source["sha256"],
        }
        trim = {
            **meta,
            "来源车系ID": str(sid),
            "销售状态": [series["scope_status"]],
            "销售状态依据": series["scope_basis"],
            "原始销售状态码": str(spec["specstate"]),
            "来源采集URL": source["url"],
            "图片来源URL清单": None,
        }
        equipment = dict(meta)
        for field in dictionary:
            target = trim if field["table"] == "trims" else equipment
            target[field["column"]] = None
            if field["kind"] == "number":
                target[raw_column(field)] = None
        for key, cell in cells.items():
            result["evidence"].append(
                {
                    "series_id": sid,
                    "spec_id": spec_id,
                    "field": key,
                    "raw_cell": cell.model_dump(),
                    "source": source,
                }
            )
            if key not in fields_by_key:
                issues.append(
                    {
                        "kind": "unknown_field",
                        "series_id": sid,
                        "spec_id": spec_id,
                        "field": key,
                        "raw_cell": cell.model_dump(),
                    }
                )
                continue
            field = fields_by_key[key]
            display = render_cell(cell)
            if field["kind"] == "number":
                target = trim if field["table"] == "trims" else equipment
                target[raw_column(field)] = cell_text(cell) or None
                value = numeric_cell(cell)
                if value is None and display not in ("", "-", "—", "暂无数据", "暂无报价"):
                    issues.append(
                        {
                            "kind": "numeric_condition",
                            "series_id": sid,
                            "spec_id": spec_id,
                            "field": key,
                            "raw_cell": cell.model_dump(),
                        }
                    )
            else:
                value = display or None
            target = trim if field["table"] == "trims" else equipment
            target[field["column"]] = value
        result["trims"].append({"key": str(spec_id), "model_key": str(sid), "fields": trim})
        result["equipment"].append(
            {"key": str(spec_id), "trim_key": str(spec_id), "fields": equipment}
        )
    return result, issues


def export_run(root: Path, dictionary_path: Path | None = None):
    assert_external(root)
    status = json.loads((root / "status.json").read_text())
    if status["phase"] != "complete" or not status["completed_series"]:
        raise ValueError("Run is incomplete; inspect status.json before exporting for writes")
    dictionary = load_dictionary(dictionary_path)
    desired = {"models": [], "trims": [], "equipment": []}
    evidence, issues = [], []
    files = sorted((root / "series").glob("*.json"))
    if {int(file.stem) for file in files} != set(status["completed_series"]):
        raise ValueError("Packet files differ from the completed series scope")
    for file in files:
        packet = json.loads(file.read_text())
        if packet["series"]["seriesid"] != int(file.stem):
            raise ValueError("Packet filename/series identity mismatch")
        for source in [
            packet["parameter_source"],
            packet["spec_list_source"],
            packet["series"]["catalog_source"],
        ]:
            raw = (root / source["file"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != source["sha256"]:
                raise ValueError(f"Source evidence hash mismatch: {source['file']}")
        parameter_raw = (root / packet["parameter_source"]["file"]).read_bytes()
        if json.loads(parameter_raw).get("result") != packet["payload"]:
            raise ValueError("Normalized parameter payload differs from its captured source")
        expanded, packet_issues = expand_packet(packet, dictionary)
        for table in desired:
            desired[table].extend(expanded[table])
        evidence.extend(expanded["evidence"])
        issues.extend(packet_issues)
    for table, rows in desired.items():
        if len({row["key"] for row in rows}) != len(rows):
            raise ValueError(f"Duplicate business keys: {table}")
    save_json(root / "field-audit.json", issues)
    save_json(root / "cell-evidence.json", evidence)
    if issues:
        raise ValueError(f"{len(issues)} field conditions require review; see field-audit.json")
    save_json(root / "desired.json", desired)
    for table, rows in desired.items():
        with (root / f"{table}.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            columns = list(rows[0]["fields"]) if rows else []
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {k: "/".join(v) if isinstance(v, list) else v for k, v in row["fields"].items()}
                )
    summary = {table: len(rows) for table, rows in desired.items()}
    summary.update(
        {
            "parameter_fields": len(dictionary),
            "source_cells": len(evidence),
            "audit_issues": 0,
            "images_downloaded": 0,
        }
    )
    save_json(root / "export-verification.json", summary)
    return summary
