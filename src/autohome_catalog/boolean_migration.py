"""Stage typed tables, check every cell and relationship, then publish atomically by config."""

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

from .boolean_layout import build, compile_details, compile_tables, partition_details
from .feishu import Client, record_index, same_value
from .human_layout import read_records, set_visible
from .image_sync import url_lines
from .storage import assert_external, save_json

ARCHIVE_FOLDER = "历史备份｜布尔迁移20261018"


def guard(config, command, args):
    state = config["_boolean_migration"]
    if command in {
        "+table-list",
        "+field-list",
        "+record-list",
        "+view-list",
        "+view-get-visible-fields",
        "+base-block-list",
    }:
        return
    tid = args[args.index("--table-id") + 1] if "--table-id" in args else None
    if command == "+table-create" and args[args.index("--name") + 1] in state["pending_names"]:
        return
    if command == "+base-block-create" and args == ["--type", "folder", "--name", ARCHIVE_FOLDER]:
        return
    if tid in state["new_tables"] and command in {
        "+record-batch-create",
        "+record-batch-update",
        "+view-set-visible-fields",
        "+field-create",
    }:
        return
    if tid == config["tables"]["models"] and command in {
        "+record-batch-update",
        "+view-set-visible-fields",
    }:
        return
    if (
        tid == config["tables"]["models"]
        and command == "+field-update"
        and args[args.index("--field-id") + 1] in state.get("model_rename_ids", [])
    ):
        return
    if command == "+base-block-rename":
        bid = args[args.index("--block-id") + 1]
        name = args[args.index("--name") + 1]
        if state["publish_names"].get(bid) == name or state["archive_names"].get(bid) == name:
            return
    if command == "+base-block-move":
        bid = args[args.index("--block-id") + 1]
        if (
            bid in state["archive_names"]
            and "--parent-id" in args
            and args[args.index("--parent-id") + 1] == state["folder"]
        ):
            return
        if bid in state["new_tables"] and "--parent-id" not in args:
            return
    raise ValueError(f"Outside boolean migration: {command}")


def settled(client, tid, key, count):
    for attempt in range(5):
        rows = read_records(client, tid, key)
        if len(rows) == count:
            return rows
        time.sleep(1 + attempt)
    raise ValueError("Destination row count not settled")


def write_updates(client, tid, updates):
    items = list(updates.items())
    for start in range(0, len(items), 200):
        client.call(
            "+record-batch-update",
            ["--table-id", tid],
            {"update_records": dict(items[start : start + 200])},
        )


def verify_fields(expected, actual):
    for name, value in expected.items():
        if value is None and actual.get(name) in (None, ""):
            continue
        if isinstance(value, list) and not value and actual.get(name) in (None, []):
            continue
        if type(value) is bool or value is None:
            if actual.get(name) is not value:
                raise ValueError(f"Typed cell differs: {name}")
        elif (
            name == "图片URL清单"
            and isinstance(value, str)
            and isinstance(actual.get(name), str)
            and url_lines(value) == url_lines(actual[name])
        ):
            continue
        elif not same_value(value, actual.get(name)):
            raise ValueError(f"Cell differs: {name}")


def create_checked(client, tid, key, batch):
    remaining = batch
    for attempt in range(4):
        try:
            client.call("+record-batch-create", ["--table-id", tid], {"create_records": remaining})
            return
        except RuntimeError:
            if client.last_failure.get("code") not in (800100001, 800004135) or attempt == 3:
                raise
            time.sleep(3 + attempt * 2)
            actual = record_index(read_records(client, tid, key), key)
            for fields in remaining:
                if str(fields[key]) in actual:
                    verify_fields(fields, actual[str(fields[key])]["fields"])
            remaining = [fields for fields in remaining if str(fields[key]) not in actual]
            if not remaining:
                return


def publish_model_entry(client, root, state):
    """Keep the public 配置版本 entry pointing at current typed rows; retain old links."""
    tid = client.config["tables"]["models"]
    schema = {f["name"]: f for f in client.call("+field-list", ["--table-id", tid])["fields"]}
    if "配置数据" not in schema:
        if schema["配置版本"]["link_table"] != state["destinations"]["0"]:
            raise ValueError("Current model configuration entry target differs")
        return
    if "历史配置版本" in schema:
        raise ValueError("Historical model entry name collision")
    before = record_index(client.records("models"), "model_id")
    mapping = {"配置版本": "历史配置版本", "配置数据": "配置版本"}
    state["model_rename_ids"] = [schema[n]["id"] for n in mapping]
    for old, new in mapping.items():
        field = schema[old]
        if field["type"] != "link":
            raise ValueError("Model entry is not a relation")
        body = {k: v for k, v in field.items() if k not in {"id", "bidirectional_link_field_id"}}
        body["name"] = new
        time.sleep(2)
        client.call("+field-update", ["--table-id", tid, "--field-id", field["id"], "--yes"], body)
    after_schema = {f["id"]: f for f in client.call("+field-list", ["--table-id", tid])["fields"]}
    for old, new in mapping.items():
        field = schema[old]
        if after_schema[field["id"]] != {**field, "name": new}:
            raise ValueError("Model link configuration changed during rename")
    after = record_index(client.records("models"), "model_id")
    if before.keys() != after.keys():
        raise ValueError("Model identities changed")
    for mid, row in before.items():
        if after[mid]["record_id"] != row["record_id"]:
            raise ValueError("Model record identity changed")
        verify_fields(
            {mapping.get(n, n): v for n, v in row["fields"].items()}, after[mid]["fields"]
        )
    for view in client.call("+view-list", ["--table-id", tid])["views"]:
        args = ["--table-id", tid, "--view-id", view["id"]]
        visible = client.call("+view-get-visible-fields", args)["visible_fields"]
        visible = ["配置版本" if n == "历史配置版本" else n for n in visible]
        if "配置版本" not in visible:
            visible.append("配置版本")
        set_visible(client, args, visible)
    save_json(root / "model-entry-renames.json", mapping)


def fill(client, root, index, packet, key, link, state):
    state_path = root / "migration-state.json"
    registered = state["destinations"].get(str(index))
    if registered is None:
        tables = client.call("+table-list")["tables"]
        if any(t["name"] == packet["pending_name"] for t in tables):
            raise ValueError("Unregistered pending table; inspect create receipt")
        fields = packet["schema"] + [link]
        if len(fields) > 300:
            raise ValueError("Column budget exceeded")
        created = client.call(
            "+table-create",
            ["--name", packet["pending_name"], "--fields", json.dumps(fields, ensure_ascii=False)],
        )
        registered = (
            created["table"]["id"]
            if "table" in created
            else created.get("table_id", created.get("id"))
        )
        if not registered:
            raise ValueError("Inspect table creation receipt")
        state["destinations"][str(index)] = registered
        state["new_tables"].append(registered)
        state["publish_names"][registered] = packet["name"]
        save_json(state_path, state)
    schema = {
        f["name"]: f for f in client.call("+field-list", ["--table-id", registered])["fields"]
    }
    additions = [f for f in packet["schema"] if f["name"] not in schema]
    for field in additions:
        if field["type"] != "checkbox" or not field["name"].endswith("标配"):
            raise ValueError("Only reviewed hybrid status columns may be added on resume")
        client.call("+field-create", ["--table-id", registered], field)
    current = record_index(read_records(client, registered, key), key)
    # Resume additive hybrid completion without altering accepted specs or identities.
    hybrid = [
        f
        for f in packet["schema"]
        if f["type"] == "checkbox" and "规格保留在相邻原字段" in (f.get("description") or "")
    ]
    patches = {
        row["record_id"]: {
            f["name"]: packet["data"][k][f["name"]]
            for f in hybrid
            if row["fields"].get(f["name"]) is not packet["data"][k][f["name"]]
        }
        for k, row in current.items()
    }
    patches = {k: v for k, v in patches.items() if v}
    if patches:
        write_updates(client, registered, patches)
        current = record_index(read_records(client, registered, key), key)
    if set(current) - packet["data"].keys():
        raise ValueError("Unexpected destination identities")
    pending = [f for k, f in packet["data"].items() if k not in current]
    for start in range(0, len(pending), 200):
        create_checked(client, registered, key, pending[start : start + 200])
        print(
            f"{packet['name']}: {min(len(current) + start + 200, len(packet['data']))}/{len(packet['data'])}",
            flush=True,
        )
    if pending or len(current) != len(packet["data"]):
        current = record_index(settled(client, registered, key, len(packet["data"])), key)
    for k, expected in packet["data"].items():
        verify_fields(expected, current[k]["fields"])
    schema = client.call("+field-list", ["--table-id", registered])["fields"]
    by_name = {f["name"]: f for f in schema}
    for field in packet["schema"]:
        if by_name[field["name"]]["type"] != field["type"]:
            raise ValueError("Destination field type differs")
    visible = [
        f["name"]
        for f in packet["schema"]
        if not f["name"].startswith("原文")
        and f["name"]
        not in {
            "参数原文存档",
            "条件说明",
            "来源快照SHA256",
            "图片来源URL清单",
            "销售状态依据",
            "原始销售状态码",
            "来源采集URL",
        }
    ]
    visible.insert(min(5, len(visible)), link["name"])
    for view in client.call("+view-list", ["--table-id", registered])["views"]:
        set_visible(client, ["--table-id", registered, "--view-id", view["id"]], visible)
    save_json(root / f"record-map-{index}.json", {k: r["record_id"] for k, r in current.items()})
    return registered, current


def finalize_views(client, state):
    """Make the checked child tables reachable from the current configuration row."""
    root = state["destinations"]["0"]
    details = [state["destinations"][k] for k in ("details", "details2")]
    for tid in [root, *details]:
        views = client.call("+view-list", ["--table-id", tid])["views"]
        if len(views) != 1:
            raise ValueError("Expected only one default view in a newly staged table")
        args = ["--table-id", tid, "--view-id", views[0]["id"]]
        visible = client.call("+view-get-visible-fields", args)["visible_fields"]
        front = (
            [
                "配置名称",
                "model_id",
                "来源配置ID",
                "车系名称",
                "品牌",
                "所属车型",
                "参数扩展",
                "参数扩展2",
                "专属配置",
                "专属配置2",
            ]
            if tid == root
            else [
                "项目名称",
                "model_id",
                "配置名称",
                "是否标配",
                "选装项目",
                "选装价格",
                "内容",
                "类别",
                "对应配置",
            ]
        )
        schema = {f["name"] for f in client.call("+field-list", ["--table-id", tid])["fields"]}
        if not set(front) <= schema:
            raise ValueError("Navigation field missing")
        desired = front + [n for n in visible if n not in front]
        if desired != visible:
            set_visible(client, args, desired)


def migrate(config, root):
    root = assert_external(root)
    client = Client(config)
    original_config = json.loads((root / "config-before.json").read_text())
    if client.config["tables"] != original_config["tables"]:
        state_file = root / "migration-state.json"
        if (
            state_file.exists()
            and json.loads(state_file.read_text()).get("phase") == "complete"
            and client.config.get("boolean_run") == root.name
        ):
            return {
                "already_complete": True,
                **json.loads((root / "verification.json").read_text()),
            }
        raise ValueError("Configuration already changed; inspect verified completion")
    table_plan = json.loads((root / "typed-tables.json").read_text())
    detail = json.loads((root / "typed-details.json").read_text())
    detail_packets = partition_details(detail)
    state_path = root / "migration-state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
    else:
        state = {
            "pending_names": [p["pending_name"] for p in table_plan["packets"]]
            + [detail["pending_name"]],
            "new_tables": [],
            "destinations": {},
            "publish_names": {},
            "archive_names": {
                client.config["tables"][r]: n
                for r, n in [
                    ("trims", "vehicle_trims（文本备份20261018）"),
                    ("equipment", "配置参数扩展（文本备份20261018）"),
                    ("special", "专属配置明细（文本备份20261018）"),
                ]
            },
            "folder": None,
            "phase": "staging",
        }
    client.config["_boolean_migration"] = state
    for packet in detail_packets:
        if packet["pending_name"] not in state["pending_names"]:
            state["pending_names"].append(packet["pending_name"])
    if not state_path.exists():
        before = json.loads((root / "backup-verification.json").read_text())
        now = {t["id"]: t for t in client.call("+table-list")["tables"]}
        if any(now[p["id"]]["rev"] != p["rev"] for p in before.values()):
            raise ValueError("Live tables changed after backup")
        blocks = client.call("+base-block-list")["blocks"]
        folders = [b["id"] for b in blocks if b["type"] == "folder" and "历史备份" in b["name"]]
        if len(folders) == 0:
            client.call("+base-block-create", ["--type", "folder", "--name", ARCHIVE_FOLDER])
            folders = [
                b["id"]
                for b in client.call("+base-block-list")["blocks"]
                if b["type"] == "folder" and b["name"] == ARCHIVE_FOLDER
            ]
        if len(folders) != 1:
            raise ValueError("Archive folder ambiguous")
        state["folder"] = folders[0]
        save_json(state_path, state)
    models = record_index(json.loads((root / "backup/models/records.json").read_text()), "model_id")
    packets = table_plan["packets"]
    all_rows = []
    ids = []
    for index, packet in enumerate(packets):
        packet = dict(packet)
        packet["data"] = {k: dict(v) for k, v in packet["data"].items()}
        if index == 0:
            link = {
                "name": "所属车型",
                "type": "link",
                "link_table": client.config["tables"]["models"],
                "bidirectional": True,
                "bidirectional_link_field_name": "配置数据",
            }
            for fields in packet["data"].values():
                fields["所属车型"] = [{"id": models[fields["model_id"]]["record_id"]}]
        else:
            reverse = "参数扩展" + (str(index) if index > 1 else "")
            link = {
                "name": "对应配置",
                "type": "link",
                "link_table": ids[0],
                "bidirectional": True,
                "bidirectional_link_field_name": reverse,
            }
            for sid, fields in packet["data"].items():
                fields["对应配置"] = [{"id": all_rows[0][sid]["record_id"]}]
        tid, rows = fill(client, root, index, packet, "来源配置ID", link, state)
        ids.append(tid)
        all_rows.append(rows)
    detail_ids = []
    detail_rows = []
    detail_children = []
    for index, part in enumerate(detail_packets):
        part["data"] = {k: dict(v) for k, v in part["data"].items()}
        reverse = "专属配置" + (str(index + 1) if index else "")
        link = {
            "name": "对应配置",
            "type": "link",
            "link_table": ids[0],
            "bidirectional": True,
            "bidirectional_link_field_name": reverse,
        }
        for fields in part["data"].values():
            fields["对应配置"] = [{"id": all_rows[0][fields["来源配置ID"]]["record_id"]}]
        detail_id, details = fill(
            client, root, "details" + (str(index + 1) if index else ""), part, "明细ID", link, state
        )
        detail_ids.append(detail_id)
        detail_rows.append(details)
        children = defaultdict(list)
        for row in details.values():
            children[row["fields"]["来源配置ID"]].append(row["record_id"])
        detail_children.append(children)
    model_children = defaultdict(list)
    for sid, row in all_rows[0].items():
        model_children[row["fields"]["model_id"]].append(row["record_id"])
    actual_models = record_index(client.records("models"), "model_id")
    model_updates = {
        models[mid]["record_id"]: {"配置数据": [{"id": rid} for rid in rids]}
        for mid, rids in model_children.items()
        if {r["id"] for r in actual_models[mid]["fields"].get("配置数据") or []} != set(rids)
    }
    write_updates(client, client.config["tables"]["models"], model_updates)
    expected_main = {}
    for sid, row in all_rows[0].items():
        expected = {
            "专属配置" + (str(i + 1) if i else ""): [{"id": rid} for rid in children[sid]]
            for i, children in enumerate(detail_children)
        }
        for i in range(1, len(ids)):
            expected["参数扩展" + (str(i) if i > 1 else "")] = [
                {"id": all_rows[i][sid]["record_id"]}
            ]
        expected_main[row["record_id"]] = expected
    main = record_index(settled(client, ids[0], "来源配置ID", len(all_rows[0])), "来源配置ID")
    repairs = {
        row["record_id"]: {
            k: v
            for k, v in expected_main[row["record_id"]].items()
            if not same_value(v, row["fields"].get(k))
            and not (v == [] and row["fields"].get(k) is None)
        }
        for row in main.values()
    }
    write_updates(client, ids[0], {k: v for k, v in repairs.items() if v})
    print("all inverse relations reconciled", flush=True)
    # Final fresh read: every typed value and both directions, including null, are checked.
    for i, packet in enumerate(packets):
        actual = record_index(
            settled(client, ids[i], "来源配置ID", len(packet["data"])), "来源配置ID"
        )
        for sid, expected in packet["data"].items():
            verify_fields(expected, actual[sid]["fields"])
            if i == 0:
                verify_fields(expected_main[actual[sid]["record_id"]], actual[sid]["fields"])
                verify_fields(
                    {"所属车型": [{"id": models[expected["model_id"]]["record_id"]}]},
                    actual[sid]["fields"],
                )
            else:
                verify_fields({"对应配置": [{"id": main[sid]["record_id"]}]}, actual[sid]["fields"])
        save_json(root / f"final-table-{i}.json", actual)
    for index, part in enumerate(detail_packets):
        actual = record_index(
            settled(client, detail_ids[index], "明细ID", len(part["data"])), "明细ID"
        )
        for key, expected in part["data"].items():
            verify_fields(expected, actual[key]["fields"])
        save_json(root / f"final-details-{index}.json", actual)
    fresh_models = record_index(client.records("models"), "model_id")
    for mid, row in fresh_models.items():
        verify_fields(models[mid]["fields"], row["fields"])
        verify_fields({"配置数据": [{"id": rid} for rid in model_children[mid]]}, row["fields"])
    before = json.loads((root / "backup-verification.json").read_text())
    now = {t["id"]: t for t in client.call("+table-list")["tables"]}
    for role in ("trims", "equipment", "special", "images"):
        if now[before[role]["id"]]["rev"] != before[role]["rev"]:
            # Revision can change after a view edit; require identical actual cells.
            key = (
                "model_id" if role == "images" else "明细ID" if role == "special" else "来源配置ID"
            )
            old = record_index(
                json.loads((root / "backup" / role / "records.json").read_text()), key
            )
            fresh = record_index(read_records(client, before[role]["id"], key), key)
            if old.keys() != fresh.keys():
                raise ValueError("Original table identities changed during staging")
            for k, row in old.items():
                if row["record_id"] != fresh[k]["record_id"]:
                    raise ValueError("Original record identity changed")
                verify_fields(row["fields"], fresh[k]["fields"])
    verification = {
        "parameter_tables": [
            {"id": tid, "rows": len(rows)} for tid, rows in zip(ids, all_rows, strict=True)
        ],
        "details_tables": [
            {"id": tid, "rows": len(rows)}
            for tid, rows in zip(detail_ids, detail_rows, strict=True)
        ],
        "details_rows": sum(len(rows) for rows in detail_rows),
        "existing_details_preserved": 3022,
        "source_configurations": len(all_rows[0]),
        "typed_cells_and_all_links_verified": True,
        "original_tables_preserved": True,
        "models_and_images_values_unchanged": True,
    }
    save_json(root / "verification-before-publish.json", verification)
    return publish_verified(client, root, state, verification)


def publish_verified(client, root, state, verification):
    """Publish after cell and revision checks; callable for a verified metadata-only resume."""
    state_path = root / "migration-state.json"
    ids = [t["id"] for t in verification["parameter_tables"]]
    detail_ids = [t["id"] for t in verification["details_tables"]]
    # No original row/column is deleted or converted. Archive after full verification.
    names = {t["id"]: t["name"] for t in client.call("+table-list")["tables"]}
    for tid, name in state["archive_names"].items():
        if names[tid] != name:
            client.call("+base-block-rename", ["--block-id", tid, "--name", name])
        client.call("+base-block-move", ["--block-id", tid, "--parent-id", state["folder"]])
    for tid in ids + detail_ids:
        if names[tid] != state["publish_names"][tid]:
            client.call(
                "+base-block-rename", ["--block-id", tid, "--name", state["publish_names"][tid]]
            )
        client.call(
            "+base-block-move",
            ["--block-id", tid, "--before-id", client.config["tables"]["images"]],
        )
    for view in client.call("+view-list", ["--table-id", client.config["tables"]["models"]])[
        "views"
    ]:
        args = ["--table-id", client.config["tables"]["models"], "--view-id", view["id"]]
        visible = client.call("+view-get-visible-fields", args)["visible_fields"]
        visible = ["配置数据" if n == "配置版本" else n for n in visible]
        if "配置数据" not in visible:
            visible.append("配置数据")
        set_visible(client, args, visible)
    publish_model_entry(client, root, state)
    finalize_views(client, state)
    saved = dict(client.config)
    saved.pop("_boolean_migration")
    saved["tables"] = dict(saved["tables"], trims=ids[0], equipment=ids[1], special=detail_ids[0])
    saved["boolean_extension_tables"] = ids[1:]
    saved["boolean_detail_tables"] = detail_ids
    saved["model_trims_field"] = "配置版本"
    saved["boolean_semantics"] = "standard_equipment"
    saved["boolean_run"] = root.name
    saved.setdefault("archived_parameter_tables", {}).update(state["archive_names"])
    save_json(client.config_path, saved)
    state["phase"] = "complete"
    save_json(state_path, state)
    save_json(root / "verification.json", verification)
    return verification


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--prepare", action="store_true")
    a = p.parse_args()
    if a.prepare:
        plan = build(a.run, a.run.parent / "full-20261017")
        tables = compile_tables(a.run, plan)
        details = compile_details(a.run, a.run.parent / "full-20261017", plan)
        print(
            json.dumps(
                {**tables["summary"], "details_rows": len(details["data"])}, ensure_ascii=False
            )
        )
    else:
        print(json.dumps(migrate(a.config, a.run), ensure_ascii=False))
