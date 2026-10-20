"""Append a verified source series to the existing category layout; never rebuild tables."""

import argparse
import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from .base_utils import verify_fields
from .colors import AVAILABILITY, compile_colors, source_list
from .colors import KEY as COLOR_KEY
from .export import verify_packet_sources
from .feishu import Client, record_index
from .image_sync import fields as image_fields
from .legacy.boolean_layout import source_key
from .legacy.compact_display import ALIASES
from .legacy.human_layout import detail_key
from .legacy.single_select import specification, state, words
from .multi_select import all_options, tokens
from .parse import cell_text, full_title, numeric_cell, parse_parameters, render_cell
from .storage import assert_external, save_json

SPECIAL = {"特色配置", "选装包"}


def guard(config, command, args):
    owned = config["_append_series"]
    tid = args[args.index("--table-id") + 1] if "--table-id" in args else None
    if command in {"+table-list", "+field-list", "+view-list", "+field-search-options"}:
        return
    if tid in owned["tables"] and command in {
        "+record-list",
        "+record-batch-create",
        "+field-update",
    }:
        return
    raise ValueError(f"Outside incremental source-series append: {command}")


def read_target(client, tid, sid, specs, models=False):
    filters = {
        "logic": "or",
        "conditions": [["来源车系ID", "==", str(sid)]]
        if models
        else [["来源配置ID", "==", str(s)] for s in specs],
    }
    rows, offset = [], 0
    while True:
        data = client.call(
            "+record-list",
            [
                "--table-id",
                tid,
                "--limit",
                "200",
                "--offset",
                str(offset),
                "--filter-json",
                json.dumps(filters, ensure_ascii=False),
            ],
        )
        rows.extend(
            {"record_id": rid, "fields": dict(zip(data["fields"], values, strict=True))}
            for rid, values in zip(data["record_id_list"], data["data"], strict=True)
        )
        if not data["has_more"]:
            return rows
        offset += len(data["data"])


def resolve_slots(conversion, catalog, category_mapping):
    """Recover source identities, including status columns whose descriptions were shortened."""
    slots = []
    for m in conversion["mapping"]:
        base = next(
            f
            for f in conversion["packets"][m["table_index"]]["schema"]
            if f["name"] == m["fields"][0]
        )
        key = source_key(base, catalog)
        if key is None:
            name = m["source_field"].removeprefix("原文｜")
            sections = {
                f["section"] for f in category_mapping["fields"] if f["name"] == m["fields"][0]
            }
            candidates = [
                f
                for f in catalog
                if name
                in {f["column"], f["name"], ALIASES.get(f["column"]), ALIASES.get(f["name"])}
                and (not sections or f["section"] in sections)
            ]
            if len(candidates) == 1:
                key = tuple(candidates[0][k] for k in ("group", "section", "item_id", "name"))
        if key:
            slots.append((key, m, base))
    return slots


def slot_values(cell, key, m, base, native):
    names = m["fields"]
    if len(names) > 1 and any(n.endswith("状态") for n in names[1:]):
        main, status, extra = specification(cell)
        values = dict(zip(names, [main, status, extra], strict=False))
    elif base["type"] == "select":
        values = {names[0]: [state(cell)] if state(cell) else None}
    elif base["type"] == "number":
        values = {names[0]: numeric_cell(cell) if cell else None}
    else:
        values = {names[0]: words(key[3], cell) if cell else None}
    for name in list(values):
        field = native.get(name)
        if field and field["type"] == "select" and field.get("multiple"):
            values[name] = tokens(values[name])
    return values


def compile_parameters(packet, schemas, slots, quotes):
    sid = packet["series"]["seriesid"]
    specs = {s["id"]: s for s in packet["spec_list"]}
    cells = parse_parameters(packet["payload"], sid, set(specs))
    cells = {
        spec: {key: cell.model_dump() for key, cell in values.items()}
        for spec, values in cells.items()
    }
    source = packet["parameter_source"]
    bread = packet["payload"]["bread"]
    ordinary = {
        name: {}
        for name in schemas
        if name not in SPECIAL | {"models", "trims", "外观颜色", "内饰颜色"}
    }
    index, colors, details, covered = {}, {}, {name: {} for name in SPECIAL}, set()
    for spec, bykey in cells.items():
        ident = {
            "品牌": bread["brandname"],
            "车系名称": bread["seriesname"],
            "配置名称": specs[spec]["specname"],
            "来源车系ID": str(sid),
            "来源配置ID": str(spec),
        }
        ident["车型全称"] = full_title(ident)
        provenance = {
            "来源采集时间": datetime.fromisoformat(source["fetched_at"]).isoformat(
                timespec="milliseconds"
            ),
            "来源采集URL": source["url"],
            "来源快照SHA256": source["sha256"],
            "来源参数页": f"https://www.autohome.com.cn/config/spec/{spec}.html",
        }
        idx = {
            **ident,
            **provenance,
            "销售状态": [packet["series"]["scope_status"]],
            "销售状态依据": packet["series"]["scope_basis"],
            "原始销售状态码": str(specs[spec]["specstate"]),
        }
        raw_colors = {}
        for key, cell in bykey.items():
            if key[1] == "颜色":
                raw_colors[key[3]] = render_cell(cell)
                covered.add(key)
            if key[1] in SPECIAL:
                if render_cell(cell):
                    fields = {
                        **ident,
                        **provenance,
                        "项目名称": key[3],
                        "类别": [key[1]],
                        "内容": words(key[3], cell),
                        "配置状态": [state(cell)] if state(cell) else None,
                        "来源项目ID": str(key[2]),
                        "参数顺序": list(bykey).index(key) + 1,
                        "明细ID": detail_key(
                            spec,
                            dict(zip(["group", "section", "item_id", "name"], key, strict=True)),
                        ),
                    }
                    details[key[1]][fields["明细ID"]] = fields
                covered.add(key)
        sides = [
            bool(source_list(raw_colors.get(side + "颜色"), side)) for side in ("外观", "内饰")
        ]
        idx["颜色资料"] = [
            AVAILABILITY[0 if all(sides) else 1 if sides[0] else 2 if sides[1] else 3]
        ]
        idx["动力类型"] = [
            {"纯电动": "BEV", "插电式混合动力": "PHEV", "增程式": "EREV", "氢燃料": "FCEV"}[
                cell_text(bykey[("参数信息", "基本参数", 55, "能源类型")])
            ]
        ]
        index[str(spec)] = idx
        idx["选配价格说明"] = (
            "\n".join(
                key[3] + "：" + str(cell["priceinfo"])
                for key, cell in bykey.items()
                if cell.get("priceinfo")
            )
            or None
        )
        colors[str(spec)] = {
            **ident,
            **raw_colors,
            "颜色原文JSON": json.dumps(raw_colors, ensure_ascii=False),
        }
        for section in ordinary:
            ordinary[section][str(spec)] = {**ident, **provenance}
        for key, m, base in slots:
            section = key[1]
            if section not in ordinary:
                continue
            native = {f["name"]: f for f in schemas[section]}
            values = slot_values(bykey.get(key), key, m, base, native)
            ordinary[section][str(spec)].update({n: v for n, v in values.items() if n in native})
            if key in bykey:
                covered.add(key)
        ordinary["基本参数"][str(spec)]["经销商报价"] = quotes[str(spec)]["display"]
    keys = {k for bykey in cells.values() for k in bykey}
    omitted = {k for k in keys if k[3] in {"长度(mm)", "宽度(mm)", "高度(mm)"}}
    missing = keys - covered - omitted
    if missing:
        raise ValueError(f"Unmapped source parameters: {sorted(missing)}")
    return (
        index,
        ordinary,
        colors,
        details,
        {
            "source_fields": len(keys),
            "covered_fields": len(covered),
            "omitted_dimensions": sorted(omitted),
        },
    )


def ensure_options(client, tid, schema, values):
    for field in schema:
        name = field["name"]
        if field["type"] != "select":
            continue
        wanted = list(dict.fromkeys(v for row in values for v in row.get(name) or []))
        known = {o["name"] for o in field.get("options", [])}
        if set(wanted) <= known:
            continue
        options = all_options(client, tid, {**field, "truncated": True})
        missing = [n for n in wanted if n not in {o["name"] for o in options}]
        if missing:
            definition = {
                k: copy.deepcopy(v)
                for k, v in field.items()
                if k
                in {"name", "type", "multiple", "options", "description", "style", "default_value"}
            }
            definition["options"] = options + [
                {"name": n, "hue": "Orange" if "选配" in n else "Blue", "lightness": "Lighter"}
                for n in missing
            ]
            client.call(
                "+field-update", ["--table-id", tid, "--field-id", field["id"], "--yes"], definition
            )
            time.sleep(1)


def append(config, source, root):
    root = assert_external(root)
    root.mkdir(parents=True, exist_ok=True)
    client = Client(config)
    if client.config.get("model_grain") != "series":
        raise ValueError("Requires the source-series category layout")
    packet = json.loads(next((source / "series").glob("*.json")).read_text())
    verify_packet_sources(source, packet)
    sid = packet["series"]["seriesid"]
    specs = sorted(s["id"] for s in packet["spec_list"])
    tables = {
        "models": client.config["tables"]["models"],
        "trims": client.config["tables"]["trims"],
        **client.config["category_tables"],
    }
    client.config["_append_series"] = {"tables": list(tables.values())}
    before_file = root / "tables-before.json"
    if not before_file.exists():
        save_json(before_file, client.call("+table-list")["tables"])
        save_json(root / "config-before.json", json.loads(config.read_text()))

    def schema(item):
        name, tid = item
        return name, client.call("+field-list", ["--table-id", tid])["fields"]

    with ThreadPoolExecutor(max_workers=3) as pool:
        schemas = dict(pool.map(schema, tables.items()))
    save_json(root / "schemas.json", schemas)
    conversion = json.loads((root.parent / "single-select-20261018/conversion.json").read_text())
    catalog = json.loads((root.parent / "full-20261017/full-field-catalog.json").read_text())
    mapping = json.loads((root.parent / "categories-20261019/field-mapping.json").read_text())
    slots = resolve_slots(conversion, catalog, mapping)
    quotes = json.loads((source / "quotes.json").read_text())
    index, ordinary, colors, details, coverage = compile_parameters(packet, schemas, slots, quotes)
    save_json(root / "source-coverage.json", coverage)
    save_json(root / "parameter-preview.json", ordinary)
    expected_by_table = {}

    def fill(name, data, key):
        tid = tables[name]
        writable = {
            f["name"]
            for f in schemas[name]
            if f["type"]
            not in {
                "formula",
                "lookup",
                "attachment",
                "created_at",
                "updated_at",
                "created_by",
                "updated_by",
            }
        }
        values = {k: {n: v for n, v in f.items() if n in writable} for k, f in data.items()}
        ensure_options(client, tid, schemas[name], list(values.values()))
        current = record_index(read_target(client, tid, sid, specs, name == "models"), key)
        if current.keys() - values.keys():
            raise ValueError(f"Unexpected existing target rows: {name}")
        for ident, row in current.items():
            verify_fields(values[ident], row["fields"])
        todo = [f for k, f in values.items() if k not in current]
        if todo:
            client.call("+record-batch-create", ["--table-id", tid], {"create_records": todo})
        for attempt in range(5):
            actual = record_index(read_target(client, tid, sid, specs, name == "models"), key)
            if actual.keys() == values.keys():
                break
            time.sleep(attempt + 1)
        if actual.keys() != values.keys():
            raise ValueError(f"Target rows not visible yet: {name}; resume without recreation")
        for ident, row in actual.items():
            verify_fields(values[ident], row["fields"])
        save_json(root / "verified" / (name.replace("/", "_") + ".json"), list(actual.values()))
        expected_by_table[name] = len(values)
        print(
            json.dumps(
                {"table": name, "verified": len(actual), "created": len(todo)}, ensure_ascii=False
            ),
            flush=True,
        )
        return actual

    # Reuse the accepted image parser/collector; writing does not modify any old model.
    power_codes = list(dict.fromkeys(f["动力类型"][0] for f in index.values()))
    if len(power_codes) != 1:
        raise ValueError("Prepare separate image groups before appending a mixed-power series")
    result = json.loads(
        (
            source.parent
            / (source.name + "-images")
            / "groups"
            / f"AUT-{sid}-{power_codes[0]}.json"
        ).read_text()
    )
    if result["target"]["series_id"] != sid or not set(result["target"]["specs"]) <= set(specs):
        raise ValueError("Image target is outside the verified source series/configurations")
    image = image_fields(result)
    meta = packet["series"]
    cover = (
        "https:" + meta["seriesimg"] if meta["seriesimg"].startswith("//") else meta["seriesimg"]
    )
    model = {
        "来源车系ID": str(sid),
        "车系名称": meta["seriesname"],
        "原车系名称": meta["seriesname"],
        "品牌": packet["payload"]["bread"]["brandname"],
        "动力类型": power_codes,
        "车系图片URL": cover,
        "来源车系页": f"https://www.autohome.com.cn/{sid}/",
        "来源快照SHA256": meta["catalog_source"]["sha256"],
        "来源采集时间": datetime.fromisoformat(meta["catalog_source"]["fetched_at"]).isoformat(
            timespec="milliseconds"
        ),
        "图片来源页": image["来源页"],
        "图片URL清单（json字符串）": image["图片URL清单"],
        "图片数": image["图片数"],
        "图片年款": image["年款"],
        "图片类别": image["图片类别"],
        "图片验证方式": image["验证方式"],
        "图片说明": image["说明"],
        "图片采集来源页": image["来源页"],
        "图片检查时间": image["检查时间"],
    }
    model["图库分组JSON"] = json.dumps(
        [
            {
                "动力类型": power_codes[0],
                "年款": image["年款"],
                "图片URL": json.loads(image["图片URL清单"]),
                "说明": image["说明"],
                "来源页": image["来源页"],
            }
        ],
        ensure_ascii=False,
    )
    parent = fill("models", {str(sid): model}, "来源车系ID")[str(sid)]
    for f in index.values():
        f["所属车型"] = [{"id": parent["record_id"]}]
    parents = fill("trims", index, "来源配置ID")
    for name, records in ordinary.items():
        for spec, f in records.items():
            f["对应配置"] = [{"id": parents[spec]["record_id"]}]
        fill(name, records, "来源配置ID")
    for name, records in details.items():
        for f in records.values():
            f["对应配置"] = [{"id": parents[f["来源配置ID"]]["record_id"]}]
        fill(name, records, "明细ID")
    source_colors = {
        k: {"fields": {**f, "对应配置": [{"id": parents[k]["record_id"]}]}}
        for k, f in colors.items()
    }
    compiled, statuses, counts = compile_colors(source_colors, parents)
    for name, side in [("外观颜色", "外观"), ("内饰颜色", "内饰")]:
        fill(name, {k: f for k, f in compiled.items() if f["部位"] == [side]}, COLOR_KEY)
    actual_model = read_target(client, tables["models"], sid, specs, True)[0]
    if {v["id"] for v in actual_model["fields"]["配置版本"]} != {
        p["record_id"] for p in parents.values()
    }:
        raise ValueError("Reverse model/configuration relation differs")
    for spec, row in parents.items():
        if row["fields"]["颜色资料"] != [statuses[spec]]:
            raise ValueError("Color availability differs")
    final_tables = {t["id"]: t for t in client.call("+table-list")["tables"]}
    before = {t["id"]: t for t in json.loads(before_file.read_text())}
    for name, n in expected_by_table.items():
        tid = tables[name]
        if final_tables[tid]["records_count"] != before[tid]["records_count"] + n:
            raise ValueError(f"Global count delta differs: {name}")
    cfg = json.loads(config.read_text())
    cfg["expected_series_count"] = final_tables[tables["models"]]["records_count"]
    save_json(config, cfg)
    verification = {
        "series_id": sid,
        "source_specs": specs,
        "models_total": cfg["expected_series_count"],
        "configurations_total": final_tables[tables["trims"]]["records_count"],
        "added": expected_by_table,
        "color_counts": counts,
        "images": len(result["images"]),
        "source_coverage": coverage,
    }
    save_json(root / "verification.json", verification)
    return verification


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(append(args.config, args.source, args.root), ensure_ascii=False))


if __name__ == "__main__":
    main()
