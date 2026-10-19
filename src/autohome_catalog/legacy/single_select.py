"""Restore compact source slots, using single-choice equipment states."""

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from ..feishu import record_index
from ..parse import render_cell
from ..storage import assert_external, save_json
from .boolean_layout import (
    FAMILIES,
    GENERIC,
    NEGATIVE,
    clean_text,
    compile_tables,
    load_cells,
    parts,
    source_key,
)
from .compact_display import compact

OPTIONS = ["标配", "选配", "无"]
FOLDER = "历史备份｜单选整理20261018"


def state(cell):
    if cell is None:
        return None
    values = []
    raw = cell.get("itemname")
    if raw in NEGATIVE:
        values.append("无")
    elif raw in {"●", "○"}:
        values.append("标配" if raw == "●" else "选配")
    for item in cell.get("sublist") or []:
        flag = item.get("value")
        if flag in {"●", "○"} | NEGATIVE:
            values.append(
                "无"
                if item.get("name") in NEGATIVE or flag in NEGATIVE
                else "标配"
                if flag == "●"
                else "选配"
            )
    return values[0] if values and len(set(values)) == 1 else None


def choice(name, description=""):
    return {
        "name": name,
        "type": "select",
        "multiple": False,
        "options": [{"name": s} for s in OPTIONS],
        "description": description or "源站●=标配，○=选配，横杠=无；未披露留空。",
    }


def words(name, cell):
    return compact(name, render_cell(cell)).replace("●", "标配").replace("○", "选配")


def specification(cell):
    """Choose standard specs first; keep separately priced optional alternatives explicit."""
    if cell is None:
        return None, None, None
    standard, optional = [], []
    for item in cell.get("sublist") or []:
        flag, name = item.get("value"), item.get("name") or ""
        if flag not in {"●", "○"} or name in NEGATIVE:
            continue
        if name in GENERIC:
            name = ""
        if item.get("priceinfo"):
            name += "（" + str(item["priceinfo"]) + "）"
        (standard if flag == "●" else optional).append(name)
    raw = cell.get("itemname")
    if raw in {"●", "○"}:
        (standard if raw == "●" else optional).append("")
    if standard or optional:
        status = "标配" if standard else "选配"
        selected = standard or optional
        text = "\n".join(dict.fromkeys(x for x in selected if x))
        if raw and raw not in {"●", "○"} | NEGATIVE:
            text = raw + ("\n" + text if text else "")
        if cell.get("priceinfo"):
            text += ("\n" if text else "") + "价格条件：" + str(cell["priceinfo"])
        extra = (
            "\n".join(dict.fromkeys(x for x in optional if x)) if standard and optional else None
        )
        return text or None, [status], extra or None
    status = state(cell)
    text = clean_text(render_cell(cell)) if status != "无" else None
    return text or None, [status] if status else None, None


def prepare(root, previous, source):
    root.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((previous / "backup-verification.json").read_text())
    # Reuse the verified immutable original backup, not the lossy current booleans.
    backup = {}
    for role in ("trims", "equipment", "special"):
        path = previous / "backup" / role / "records.json"
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest[role]["sha256"]:
            raise ValueError("Original backup hash mismatch")
        backup[role] = json.loads(path.read_text())
    trims = record_index(backup["trims"], "来源配置ID")
    cells = load_cells(source, set(trims))
    catalog = json.loads((source / "full-field-catalog.json").read_text())
    updates = {role: {} for role in ("trims", "equipment")}
    kinds, specs, prices = {}, {}, defaultdict(list)
    spec_options = {}
    old_plan = json.loads((previous / "boolean-plan.json").read_text())
    spec_fields = {f["source_field"] for f in old_plan["features"] if f.get("preserve_source")}
    spec_fields |= {"近光灯光源", "远光灯光源"}
    for role in updates:
        schema = json.loads((previous / "backup" / role / "schema.json").read_text())
        rows = record_index(backup[role], "来源配置ID")
        for field in schema:
            key = source_key(field, catalog)
            if key is None:
                continue
            by_id = {sid: cells[sid].get(key) for sid in trims}
            present = [c for c in by_id.values() if c is not None]
            extracted = [parts(c) for c in present]
            pure = (
                field["type"] == "text"
                and any(extracted)
                and all(
                    c.get("itemname") in (None, "", "●", "○", *NEGATIVE)
                    and all(label in GENERIC | NEGATIVE for label, status, price in p)
                    for c, p in zip(present, extracted, strict=True)
                )
            )
            spec = (
                field["type"] == "text"
                and not pure
                and field["name"] in spec_fields
                and key[3] not in FAMILIES
                and any(extracted)
            )
            if pure:
                kinds[role, field["name"]] = choice(field["name"])
            elif spec:
                status_name = {"近光灯光源": "近光灯状态", "远光灯光源": "远光灯状态"}.get(
                    field["name"], field["name"] + "状态"
                )
                specs[role, field["name"]] = (
                    choice(status_name, "状态对应相邻主规格；同时存在的其他选配规格单独保存。"),
                    {},
                )
                spec_options[role, field["name"]] = (
                    {"name": field["name"] + "选配规格", "type": "text"},
                    {},
                )
            for sid, row in rows.items():
                cell = by_id[sid]
                if cell is None:
                    value = None if pure else row["fields"].get(field["name"])
                elif pure:
                    value = [state(cell)] if state(cell) else None
                    quotes = list(dict.fromkeys(str(p) for label, status, p in parts(cell) if p))
                    if quotes:
                        prices[sid].append(field["name"] + "：" + "；".join(quotes))
                elif field["type"] == "text":
                    value = words(key[3], cell)
                    if spec:
                        value, _, _ = specification(cell)
                else:
                    value = row["fields"].get(field["name"])
                updates[role].setdefault(row["record_id"], {})[field["name"]] = value
                if spec:
                    value, status, extra = specification(cell)
                    specs[role, field["name"]][1][sid] = status
                    spec_options[role, field["name"]][1][sid] = extra
    # Existing compiler supplies the original ordered slots; rebudget only whole slots.
    plan = {"issues": [], "features": [], "options": [], "updates": updates}
    base = compile_tables(previous, plan, output=root / "compact-base.json")
    first_name = base["mapping"][0]["fields"][0]
    first = base["packets"][0]
    prefix = first["schema"][
        : next(i for i, f in enumerate(first["schema"]) if f["name"] == first_name)
    ]
    prefix = prefix + [
        {
            "name": "选配价格说明",
            "type": "text",
            "description": "纯状态字段转换为单选后，源站价格条件在此完整保留；不合计重复的整体报价。",
        }
    ]
    identity = ["配置名称", "model_id", "来源配置ID", "车系名称", "品牌"]
    id_schema = [next(f for f in prefix if f["name"] == n) for n in identity]
    definitions = list(prefix)
    data = {
        sid: {
            **{f["name"]: first["data"][sid].get(f["name"]) for f in prefix},
            "选配价格说明": "\n".join(prices[sid]) or None,
        }
        for sid in trims
    }
    packets, mapping = [], []
    for m in base["mapping"]:
        old = base["packets"][m["table_index"]]
        field = next(f for f in old["schema"] if f["name"] == m["fields"][0])
        key = (m["source_role"], m["source_field"])
        block = [(kinds.get(key, field), {sid: old["data"][sid][field["name"]] for sid in trims})]
        if key in specs:
            block.append(specs[key])
            if any(spec_options[key][1].values()):
                block.append(spec_options[key])
        if len(definitions) + len(block) > 285:
            packets.append({"schema": definitions, "data": data})
            definitions = list(id_schema)
            data = {sid: {n: first["data"][sid][n] for n in identity} for sid in trims}
        for definition, values in block:
            definitions.append(definition)
            for sid, value in values.items():
                data[sid][definition["name"]] = value
        mapping.append({**m, "table_index": len(packets), "fields": [f["name"] for f, v in block]})
    packets.append({"schema": definitions, "data": data})
    for i, p in enumerate(packets):
        p["name"] = "vehicle_trims" if i == 0 else "配置参数扩展" + (str(i) if i > 1 else "")
        p["pending_name"] = p["name"] + "（单选整理中）"
        assert len({f["name"] for f in p["schema"]}) == len(p["schema"])
    if len(packets) != 2:
        raise ValueError(
            f"Compact layout did not fit two parameter tables: {[len(p['schema']) for p in packets]}, specs={len(specs)}; no writes"
        )
    special_schema = [
        {k: v for k, v in f.items() if k != "id"}
        for f in json.loads((previous / "backup/special/schema.json").read_text())
        if f["type"] != "link"
    ]
    special_schema += [choice("配置状态")]
    special_data = {}
    for row in backup["special"]:
        fields = {k: v for k, v in row["fields"].items() if k != "对应配置"}
        matches = [
            c
            for key, c in cells[fields["来源配置ID"]].items()
            if key[0] == "个性化" and str(key[2]) == fields["来源项目ID"]
        ]
        if len(matches) != 1 or state(matches[0]) is None:
            raise ValueError("Special source status ambiguous")
        fields["配置状态"] = [state(matches[0])]
        fields["内容"] = words(fields["项目名称"], matches[0])
        special_data[fields["明细ID"]] = fields
    details = {
        "name": "专属配置明细",
        "pending_name": "专属配置明细（单选整理中）",
        "schema": special_schema,
        "data": special_data,
    }
    result = {
        "packets": packets,
        "mapping": mapping,
        "details": details,
        "summary": {
            "configurations": len(trims),
            "parameter_tables": len(packets),
            "columns": [len(p["schema"]) for p in packets],
            "original_slots": len(mapping),
            "pure_single_choice_fields": len(kinds),
            "spec_status_fields": len(specs),
            "special_rows": len(special_data),
        },
    }
    save_json(root / "conversion.json", result)
    save_json(root / "字段映射.json", mapping)
    return result


def migrate(config, root, previous):
    from ..base_utils import read_records, set_visible
    from ..feishu import Client, record_index
    from .boolean_migration import fill, publish_model_entry, verify_fields, write_updates

    root = assert_external(root)
    plan = json.loads((root / "conversion.json").read_text())
    client = Client(config)
    state_path = root / "migration-state.json"
    if state_path.exists():
        state_data = json.loads(state_path.read_text())
        if state_data["phase"] == "complete":
            return json.loads((root / "verification.json").read_text())
    else:
        state_data = {
            "phase": "backup",
            "new_tables": [],
            "destinations": {},
            "pending_names": [p["pending_name"] for p in plan["packets"]]
            + [plan["details"]["pending_name"]],
            "publish_names": {},
            "archive_names": {},
            "folder": None,
            "archive_folder_name": FOLDER,
        }
        save_json(root / "config-before.json", client.config)
        old_ids = [
            client.config["tables"]["trims"],
            *client.config["boolean_extension_tables"],
            *client.config["boolean_detail_tables"],
        ]
        current = {t["id"]: t for t in client.call("+table-list")["tables"]}
        state_data["archive_names"] = {
            tid: current[tid]["name"] + "（布尔备份20261018）" for tid in old_ids
        }
        save_json(state_path, state_data)
    client.config["_boolean_migration"] = state_data
    before_path = root / "current-backup.json"
    if not before_path.exists():
        current = {t["id"]: t for t in client.call("+table-list")["tables"]}
        cutoff = (previous / "published-layout-verification.json").stat().st_mtime_ns
        receipt = max(
            f
            for f in client.root.glob("*table-list.response.json")
            if int(f.name.split("-")[0]) < cutoff
        )
        cached_meta = {t["id"]: t for t in json.loads(receipt.read_text())["data"]["tables"]}
        cached_ids = [
            client.config["tables"]["trims"],
            *client.config["boolean_extension_tables"],
            *client.config["boolean_detail_tables"],
        ]
        backups = {}
        for i, tid in enumerate(cached_ids):
            path = root / "backup" / (tid + ".json")
            if current[tid]["rev"] == cached_meta[tid]["rev"]:
                source_file = previous / (
                    f"final-table-{i}.json" if i < 3 else f"final-details-{i - 3}.json"
                )
                rows = list(json.loads(source_file.read_text()).values())
                mode = "revision-matched-cache"
            else:
                rows = read_records(client, tid, "来源配置ID" if i < 3 else "明细ID")
                mode = "fresh-cli-read"
            save_json(path, rows)
            save_json(
                root / "backup" / (tid + "-schema.json"),
                client.call("+field-list", ["--table-id", tid]),
            )
            backups[tid] = {
                "rev": current[tid]["rev"],
                "rows": len(rows),
                "mode": mode,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        models = client.records("models")
        save_json(root / "models-before.json", models)
        after = {t["id"]: t for t in client.call("+table-list")["tables"]}
        if any(after[tid]["rev"] != item["rev"] for tid, item in backups.items()):
            raise ValueError("Source tables changed during backup")
        save_json(
            before_path,
            {
                "tables": backups,
                "models_rev": after[client.config["tables"]["models"]]["rev"],
                "images_rev": after[client.config["tables"]["images"]]["rev"],
            },
        )
        print("Current backup verified; unchanged tables reused by exact revision", flush=True)
    proof = json.loads(before_path.read_text())
    now = {t["id"]: t for t in client.call("+table-list")["tables"]}
    for tid, item in proof["tables"].items():
        if now[tid]["rev"] != item["rev"]:
            raise ValueError("An accepted source table changed; no overwrite")
    if state_data["folder"] is None:
        found = [b["id"] for b in client.call("+base-block-list")["blocks"] if b["name"] == FOLDER]
        if not found:
            client.call("+base-block-create", ["--type", "folder", "--name", FOLDER])
            found = [
                b["id"] for b in client.call("+base-block-list")["blocks"] if b["name"] == FOLDER
            ]
        if len(found) != 1:
            raise ValueError("Archive folder ambiguous")
        state_data["folder"] = found[0]
        save_json(state_path, state_data)
    models = record_index(json.loads((root / "models-before.json").read_text()), "model_id")
    all_rows, tids = [], []
    for i, original in enumerate(plan["packets"]):
        packet = {**original, "data": {k: dict(v) for k, v in original["data"].items()}}
        link = {
            "name": "所属车型" if i == 0 else "对应配置",
            "type": "link",
            "bidirectional": True,
            "link_table": client.config["tables"]["models"] if i == 0 else tids[0],
            "bidirectional_link_field_name": "配置数据" if i == 0 else "参数扩展",
        }
        for sid, fields in packet["data"].items():
            fields[link["name"]] = [
                {
                    "id": models[fields["model_id"]]["record_id"]
                    if i == 0
                    else all_rows[0][sid]["record_id"]
                }
            ]
        tid, rows = fill(client, root, i, packet, "来源配置ID", link, state_data)
        tids.append(tid)
        all_rows.append(rows)
        save_json(root / f"final-parameter-{i}.json", rows)
    detail = {**plan["details"], "data": {k: dict(v) for k, v in plan["details"]["data"].items()}}
    for fields in detail["data"].values():
        fields["对应配置"] = [{"id": all_rows[0][fields["来源配置ID"]]["record_id"]}]
    dtid, details = fill(
        client,
        root,
        "details",
        detail,
        "明细ID",
        {
            "name": "对应配置",
            "type": "link",
            "link_table": tids[0],
            "bidirectional": True,
            "bidirectional_link_field_name": "专属配置",
        },
        state_data,
    )
    save_json(root / "final-details.json", details)
    special_children = defaultdict(list)
    model_children = defaultdict(list)
    for row in details.values():
        special_children[row["fields"]["来源配置ID"]].append({"id": row["record_id"]})
    root_rows = record_index(read_records(client, tids[0], "来源配置ID"), "来源配置ID")
    patches = {}
    for sid, row in root_rows.items():
        verify_fields(plan["packets"][0]["data"][sid], row["fields"])
        expected = {
            "参数扩展": [{"id": all_rows[1][sid]["record_id"]}],
            "专属配置": special_children[sid],
        }
        try:
            verify_fields(expected, row["fields"])
        except ValueError:
            patches[row["record_id"]] = expected
        model_children[row["fields"]["model_id"]].append({"id": row["record_id"]})
    if patches:
        write_updates(client, tids[0], patches)
        root_rows = record_index(read_records(client, tids[0], "来源配置ID"), "来源配置ID")
        for sid, row in root_rows.items():
            verify_fields(
                {
                    "参数扩展": [{"id": all_rows[1][sid]["record_id"]}],
                    "专属配置": special_children[sid],
                },
                row["fields"],
            )
            verify_fields(plan["packets"][0]["data"][sid], row["fields"])
    save_json(root / "final-parameter-0.json", root_rows)
    actual_models = record_index(client.records("models"), "model_id")
    model_updates = {}
    for mid, row in actual_models.items():
        verify_fields(models[mid]["fields"], row["fields"])
        expected = {"配置数据": model_children[mid]}
        try:
            verify_fields(expected, row["fields"])
        except ValueError:
            model_updates[row["record_id"]] = expected
    write_updates(client, client.config["tables"]["models"], model_updates)
    if model_updates:
        for mid, row in record_index(client.records("models"), "model_id").items():
            verify_fields({**models[mid]["fields"], "配置数据": model_children[mid]}, row["fields"])
    now = {t["id"]: t for t in client.call("+table-list")["tables"]}
    if any(now[tid]["rev"] != item["rev"] for tid, item in proof["tables"].items()):
        raise ValueError("Original values changed during staging")
    if now[client.config["tables"]["images"]]["rev"] != proof["images_rev"]:
        raise ValueError("Image table changed externally; inspect before publishing")
    verification = {
        **plan["summary"],
        "parameter_ids": tids,
        "special_id": dtid,
        "all_values_types_and_links_verified": True,
        "inverse_repairs": len(patches),
        "old_slots_preserved": len(plan["mapping"]),
        "images_untouched": True,
    }
    save_json(root / "verified-before-publish.json", verification)
    print("All values and relations verified; publishing compact tables", flush=True)
    for tid, name in state_data["archive_names"].items():
        client.call("+base-block-rename", ["--block-id", tid, "--name", name])
        client.call("+base-block-move", ["--block-id", tid, "--parent-id", state_data["folder"]])
    for tid in tids + [dtid]:
        client.call(
            "+base-block-rename", ["--block-id", tid, "--name", state_data["publish_names"][tid]]
        )
        client.call(
            "+base-block-move",
            ["--block-id", tid, "--before-id", client.config["tables"]["images"]],
        )
    for view in client.call("+view-list", ["--table-id", tids[0]])["views"]:
        args = ["--table-id", tids[0], "--view-id", view["id"]]
        visible = client.call("+view-get-visible-fields", args)["visible_fields"]
        front = [
            "配置名称",
            "model_id",
            "来源配置ID",
            "车系名称",
            "品牌",
            "所属车型",
            "参数扩展",
            "专属配置",
        ]
        set_visible(client, args, front + [n for n in visible if n not in front])
    publish_model_entry(client, root, state_data)
    saved = dict(client.config)
    saved.pop("_boolean_migration")
    for key in [
        "boolean_extension_tables",
        "boolean_detail_tables",
        "boolean_run",
        "boolean_semantics",
    ]:
        saved.pop(key, None)
    saved["tables"] = {**saved["tables"], "trims": tids[0], "equipment": tids[1], "special": dtid}
    saved.update(
        status_run=root.name,
        parameter_extension_tables=tids[1:],
        detail_tables=[dtid],
        status_semantics="标配/选配/无;未披露为空",
        model_trims_field="配置版本",
    )
    save_json(config, saved)
    state_data["phase"] = "complete"
    save_json(state_path, state_data)
    save_json(root / "verification.json", verification)
    return verification


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    previous = args.run.parent / "boolean-20261018"
    if args.prepare:
        print(
            json.dumps(
                prepare(args.run, previous, args.run.parent / "full-20261017")["summary"],
                ensure_ascii=False,
            )
        )
    else:
        print(json.dumps(migrate(args.config, args.run, previous), ensure_ascii=False))
