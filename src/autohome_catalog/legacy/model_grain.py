"""Backed-up migration to one model_id per model; retain every historical link."""

import hashlib
import json
import re
import time
from collections import Counter, defaultdict

from ..base_utils import read_records, set_visible
from ..feishu import Client, record_index, same_value
from ..storage import assert_external, save_json

PENDING = "vehicle_models（动力拆分中）"
ARCHIVE = "vehicle_models（按车系备份20261018）"
RELATION = "所属车型"
POWER = {"BEV": "纯电动", "PHEV": "插电式混合动力", "EREV": "增程式", "FCEV": "氢燃料"}
KEYS = {
    "models": "来源车系ID",
    "trims": "来源配置ID",
    "images": "model_id",
    "equipment": "来源配置ID",
    "special": "明细ID",
}


def guard(config, command, args):
    state = config["_model_grain_operation"]
    if command in {
        "+table-list",
        "+field-list",
        "+record-list",
        "+view-list",
        "+view-get-visible-fields",
        "+view-get-sort",
        "+view-get-filter",
        "+view-get-group",
        "+base-block-list",
    }:
        return
    if command == "+table-create" and args[args.index("--name") + 1] == PENDING:
        return
    tid = args[args.index("--table-id") + 1] if "--table-id" in args else None
    children = {config["tables"][r] for r in ("trims", "images")}
    if (
        tid == state.get("new_models")
        and tid
        and command in {"+record-batch-create", "+record-batch-update", "+view-set-visible-fields"}
    ):
        return
    if tid in children and command in {
        "+field-create",
        "+record-batch-update",
        "+view-set-visible-fields",
    }:
        return
    if command == "+base-block-rename":
        bid, name = args[args.index("--block-id") + 1], args[args.index("--name") + 1]
        if (bid == state.get("new_models") and name == "vehicle_models") or (
            bid == state["old_models"] and name == ARCHIVE
        ):
            return
    if command == "+base-block-move":
        bid = args[args.index("--block-id") + 1]
        if (
            bid == state["old_models"]
            and "--parent-id" in args
            and args[args.index("--parent-id") + 1] == state["folder"]
        ):
            return
        if bid == state.get("new_models") and "--parent-id" not in args:
            return
    raise ValueError(f"Outside model-grain migration: {command}")


def backup(client, root):
    original_config = dict(client.config)
    original_config.pop("_model_grain_operation", None)
    save_json(root / "config-before.json", original_config)
    tables = client.call("+table-list")["tables"]
    save_json(root / "tables-before.json", tables)
    roles = {}
    for role, key in KEYS.items():
        tid = client.config["tables"][role]
        schema = client.call("+field-list", ["--table-id", tid])["fields"]
        save_json(root / "backup" / role / "schema.json", schema)
        rows = read_records(client, tid, key)
        path = root / "backup" / role / "records.json"
        save_json(path, rows)
        views = client.call("+view-list", ["--table-id", tid])["views"]
        for view in views:
            args = ["--table-id", tid, "--view-id", view["id"]]
            view["visible"] = client.call("+view-get-visible-fields", args)["visible_fields"]
        save_json(root / "backup" / role / "views.json", views)
        before = next(t for t in tables if t["id"] == tid)
        roles[role] = {
            "id": tid,
            "count": len(rows),
            "rev": before["rev"],
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        print(f"backup {role}: {len(rows)}", flush=True)
    current = {t["id"]: t for t in client.call("+table-list")["tables"]}
    if any(current[v["id"]]["rev"] != v["rev"] for v in roles.values()):
        raise ValueError("Live tables changed during backup; no migration is permitted")
    save_json(root / "backup-verification.json", roles)
    return roles


def load_backup(root):
    proofs = json.loads((root / "backup-verification.json").read_text())
    data = {}
    for role, proof in proofs.items():
        path = root / "backup" / role / "records.json"
        if hashlib.sha256(path.read_bytes()).hexdigest() != proof["sha256"]:
            raise ValueError("Backup hash changed")
        data[role] = json.loads(path.read_text())
    return data


def build_plan(data, schemas):
    models = record_index(data["models"], "来源车系ID")
    groups = defaultdict(list)
    for row in data["trims"]:
        f = row["fields"]
        mid = f["model_id"]
        m = re.fullmatch(r"AUT-(\d+)-(BEV|PHEV|EREV|FCEV)", mid)
        if not m or m[1] != str(f["来源车系ID"]) or str(f["来源车系ID"]) not in models:
            raise ValueError(f"Invalid model/series identity: {mid}")
        if f.get("所属车系") != [{"id": models[m[1]]["record_id"]}]:
            raise ValueError("Trim old parent does not match source series")
        if not str(f["能源类型"]).startswith(POWER[m[2]]):
            raise ValueError(f"Powertrain mismatch: {mid}/{f['能源类型']}")
        groups[mid].append(row["record_id"])
    images = record_index(data["images"], "model_id")
    if groups.keys() != images.keys():
        raise ValueError("Image/trim model_id sets differ")
    if {mid.split("-")[1] for mid in groups} != models.keys():
        raise ValueError("Series coverage differs")
    new_rows = []
    for mid in sorted(groups):
        sid, code = mid.split("-")[1:]
        original = models[sid]
        f = original["fields"]
        image = images[mid]["fields"]
        if image["所属车系"] != [{"id": original["record_id"]}]:
            raise ValueError("Image old parent mismatch")
        allowed = set((f.get("model_id") or "").splitlines())
        if mid not in allowed:
            raise ValueError("Original model cell does not contain its configuration model_id")
        name = f"{f['车系名称']}（{code}）"
        values = {
            definition["name"]: f.get(definition["name"])
            for definition in schemas
            if definition["type"] != "link"
        }
        # Existing URL-style cells may be returned as Markdown by lark CLI.
        for k, v in values.items():
            if isinstance(v, str):
                match = re.fullmatch(r"\[[^\]]*\]\((https?://[^\s]+)\)", v)
                if match:
                    values[k] = match[1]
        values.update(车系名称=name, model_id=mid, 原车系名称=f["车系名称"], 动力类型=POWER[code])
        new_rows.append(
            {
                "key": mid,
                "fields": values,
                "trim_ids": groups[mid],
                "image_record": images[mid]["record_id"],
            }
        )
    collisions = Counter(row["fields"]["车系名称"] for row in new_rows)
    for row in new_rows:
        f = row["fields"]
        if collisions[f["车系名称"]] > 1:
            f["车系名称"] = f"{f['品牌']} {f['车系名称']}〔{f['来源车系ID']}〕"
    if len({row["fields"]["车系名称"] for row in new_rows}) != len(new_rows):
        raise ValueError("Display names still collide")
    for role in ("equipment", "special"):
        trim_ids = {r["record_id"] for r in data["trims"]}
        if any(
            len(r["fields"].get("对应配置") or []) != 1
            or r["fields"]["对应配置"][0]["id"] not in trim_ids
            for r in data[role]
        ):
            raise ValueError("Invalid dependent configuration relation")
    return {
        "rows": new_rows,
        "counts": {r: len(v) for r, v in data.items()},
        "new_models": len(new_rows),
        "new_model_names_unique": True,
        "all_trim_image_parents_unique": True,
    }


def table_rows(client, tid, key, expected_count=None):
    for attempt in range(7):
        rows = read_records(client, tid, key)
        if expected_count is None or len(rows) == expected_count:
            return rows
        time.sleep(1 + attempt)
    raise ValueError("Record count did not settle after bounded read-back")


def update_links(client, tid, patches):
    items = list(patches.items())
    for offset in range(0, len(items), 200):
        client.call(
            "+record-batch-update",
            ["--table-id", tid],
            {"update_records": dict(items[offset : offset + 200])},
        )


def migrate(config_path, root, prepare_only=False):
    root = assert_external(root)
    root.mkdir(parents=True, exist_ok=True)
    client = Client(config_path)
    state_path = root / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
    else:
        state = {
            "old_models": client.config["tables"]["models"],
            "folder": None,
            "phase": "preparing",
        }
    client.config["_model_grain_operation"] = state
    if not (root / "backup-verification.json").exists():
        backup(client, root)
    data = load_backup(root)
    schema = json.loads((root / "backup/models/schema.json").read_text())
    plan = build_plan(data, schema)
    save_json(root / "plan.json", plan)
    print(
        json.dumps({k: v for k, v in plan.items() if k != "rows"}, ensure_ascii=False), flush=True
    )
    if prepare_only:
        save_json(state_path, state)
        return {"phase": "preflight_passed", "new_models": plan["new_models"]}
    if state.get("phase") == "preparing":
        current = {t["id"]: t for t in client.call("+table-list")["tables"]}
        proofs = json.loads((root / "backup-verification.json").read_text())
        if any(current[p["id"]]["rev"] != p["rev"] for p in proofs.values()):
            raise ValueError("Tables changed after validated backup; abort before writes")
        blocks = client.call("+base-block-list")
        save_json(root / "blocks-before.json", blocks)

        # Reuse the already-established archive folder, never invent a folder ID.
        def walk(value):
            if isinstance(value, dict):
                if value.get("type") == "folder" and "历史备份" in value.get("name", ""):
                    yield value
                for v in value.values():
                    yield from walk(v)
            elif isinstance(value, list):
                for v in value:
                    yield from walk(v)

        folders = list(walk(blocks))
        if len(folders) != 1:
            raise ValueError("Archive folder is not unambiguous")
        state["folder"] = folders[0]["id"]
        if any(t["name"] == PENDING for t in current.values()):
            raise ValueError("Unregistered pending table; inspect receipts before creating")
        definitions = [
            {
                k: v
                for k, v in field.items()
                if k in {"name", "type", "description", "style", "default_value"}
            }
            for field in schema
            if field["type"] != "link"
        ]
        definitions.sort(key=lambda x: (x["name"] != "车系名称", x["name"] != "model_id"))
        next(f for f in definitions if f["name"] == "model_id")["description"] = (
            "唯一车型编号，一行一个AUT-车系ID-动力代码；不可换行或合并多个编号。"
        )
        definitions += [{"name": n, "type": "text"} for n in ["原车系名称", "动力类型"]]
        result = client.call(
            "+table-create",
            ["--name", PENDING, "--fields", json.dumps(definitions, ensure_ascii=False)],
        )
        state["new_models"] = (
            result["table"]["id"] if "table" in result else result.get("table_id", result.get("id"))
        )
        if not state["new_models"]:
            raise ValueError("Inspect table creation receipt")
        state["phase"] = "created"
        save_json(state_path, state)
    tid = state["new_models"]
    current = record_index(table_rows(client, tid, "model_id"), "model_id")
    if set(current) - {r["key"] for r in plan["rows"]}:
        raise ValueError("Unexpected models in destination")
    pending = [r["fields"] for r in plan["rows"] if r["key"] not in current]
    for offset in range(0, len(pending), 200):
        client.call(
            "+record-batch-create",
            ["--table-id", tid],
            {"create_records": pending[offset : offset + 200]},
        )
    current = record_index(table_rows(client, tid, "model_id", plan["new_models"]), "model_id")
    for row in plan["rows"]:
        if any(
            not same_value(v, current[row["key"]]["fields"].get(k))
            for k, v in row["fields"].items()
        ):
            raise ValueError("New model value differs after read-back")
    save_json(root / "model-record-map.json", {k: r["record_id"] for k, r in current.items()})
    for role, reverse in [("trims", "配置版本"), ("images", "图片组")]:
        child = client.config["tables"][role]
        fields = client.call("+field-list", ["--table-id", child])["fields"]
        existing = next((f for f in fields if f["name"] == RELATION), None)
        if existing and (
            existing["type"] != "link"
            or existing["link_table"] != tid
            or not existing.get("bidirectional")
        ):
            raise ValueError("Destination model relation exists with a different target")
        if not existing:
            client.call(
                "+field-create",
                ["--table-id", child],
                [
                    {
                        "name": RELATION,
                        "type": "link",
                        "link_table": tid,
                        "bidirectional": True,
                        "bidirectional_link_field_name": reverse,
                    }
                ],
            )
        rows = table_rows(client, child, KEYS[role])
        patches = {}
        for record in rows:
            mid = record["fields"]["model_id"]
            expected = [{"id": current[mid]["record_id"]}]
            patch = {RELATION: expected} if record["fields"].get(RELATION) != expected else {}
            if role == "images" and record["fields"]["车型"] != current[mid]["fields"]["车系名称"]:
                patch["车型"] = current[mid]["fields"]["车系名称"]
            if patch:
                patches[record["record_id"]] = patch
        update_links(client, child, patches)
        print(f"linked {role}: {len(patches)}", flush=True)
    reconcile_reverse(client, root, plan, state)
    verification = verify(client, root, plan, state, data)
    # Only publish table names/configuration after every data and bidirectional link check passed.
    names = {t["id"]: t["name"] for t in client.call("+table-list")["tables"]}
    if names[state["old_models"]] != ARCHIVE:
        client.call("+base-block-rename", ["--block-id", state["old_models"], "--name", ARCHIVE])
    if names[tid] != "vehicle_models":
        client.call("+base-block-rename", ["--block-id", tid, "--name", "vehicle_models"])
    client.call(
        "+base-block-move", ["--block-id", state["old_models"], "--parent-id", state["folder"]]
    )
    client.call(
        "+base-block-move", ["--block-id", tid, "--before-id", client.config["tables"]["images"]]
    )
    for role in ("trims", "images"):
        child = client.config["tables"][role]
        views = client.call("+view-list", ["--table-id", child])["views"]
        for view in views:
            args = ["--table-id", child, "--view-id", view["id"]]
            current_visible = client.call("+view-get-visible-fields", args)["visible_fields"]
            visible = [RELATION if n == "所属车系" else n for n in current_visible]
            if RELATION not in visible:
                visible.append(RELATION)
            set_visible(client, ["--table-id", child, "--view-id", view["id"]], visible)
    visible = [
        "车系名称",
        "model_id",
        "动力类型",
        "品牌",
        "原车系名称",
        "来源车系ID",
        "配置版本",
        "图片组",
        "来源车系页",
        "来源采集时间",
        "来源快照SHA256",
    ]
    for view in client.call("+view-list", ["--table-id", tid])["views"]:
        set_visible(client, ["--table-id", tid, "--view-id", view["id"]], visible)
    saved = dict(client.config)
    saved.pop("_model_grain_operation")
    saved["tables"] = dict(saved["tables"], models=tid)
    saved["model_grain"] = "series_powertrain"
    saved["key_fields"] = {**saved.get("key_fields", {}), "models": "model_id"}
    saved["model_relation_field"] = RELATION
    saved["models_image_field"] = "图片组"
    saved.setdefault("archived_model_tables", {})[state["old_models"]] = ARCHIVE
    save_json(client.config_path, saved)
    state["phase"] = "complete"
    save_json(state_path, state)
    save_json(root / "verification.json", verification)
    return verification


def reconcile_reverse(client, root, plan, state):
    """Set each inverse cell as one complete list, avoiding per-child update races."""
    tid = state["new_models"]
    rows = record_index(table_rows(client, tid, "model_id", plan["new_models"]), "model_id")
    patches = {}
    for expected in plan["rows"]:
        row = rows[expected["key"]]
        fields = {
            "配置版本": [{"id": rid} for rid in expected["trim_ids"]],
            "图片组": [{"id": expected["image_record"]}],
        }
        patch = {
            name: value
            for name, value in fields.items()
            if not same_value(row["fields"].get(name), value)
        }
        if patch:
            patches[row["record_id"]] = patch
    if patches:
        save_json(root / "reverse-repair-plan.json", patches)
    update_links(client, tid, patches)
    print(f"reverse relation reconciliation: {len(patches)}", flush=True)


def verify(client, root, plan, state, before):
    mid_rows = record_index(
        table_rows(client, state["new_models"], "model_id", plan["new_models"]), "model_id"
    )
    after = {}
    allowed = {
        "trims": {RELATION},
        "images": {RELATION, "车型"},
        "equipment": set(),
        "special": set(),
        "models": set(),
    }
    for role in KEYS:
        table = state["old_models"] if role == "models" else client.config["tables"][role]
        rows = table_rows(client, table, KEYS[role], len(before[role]))
        old = {r["record_id"]: r for r in before[role]}
        if {r["record_id"] for r in rows} != old.keys():
            raise ValueError(f"{role}: record identities changed")
        for row in rows:
            for name, value in old[row["record_id"]]["fields"].items():
                if name not in allowed[role] and not same_value(value, row["fields"].get(name)):
                    raise ValueError(f"{role}/{row['record_id']}/{name}: original value changed")
        after[role] = {r["record_id"]: r for r in rows}
    for row in plan["rows"]:
        model = mid_rows[row["key"]]
        if any(
            not same_value(value, model["fields"].get(name))
            for name, value in row["fields"].items()
        ):
            raise ValueError("New model values changed before final verification")
        if {r["id"] for r in model["fields"].get("配置版本") or []} != set(row["trim_ids"]):
            raise ValueError("Reverse trim relation mismatch")
        if model["fields"].get("图片组") != [{"id": row["image_record"]}]:
            raise ValueError("Reverse image relation mismatch")
        for record in [after["trims"][rid] for rid in row["trim_ids"]] + [
            after["images"][row["image_record"]]
        ]:
            if record["fields"][RELATION] != [{"id": model["record_id"]}]:
                raise ValueError("Child model relation mismatch")
            if record["fields"]["model_id"] != row["key"]:
                raise ValueError("Child model_id differs from parent")
        if after["images"][row["image_record"]]["fields"]["车型"] != model["fields"]["车系名称"]:
            raise ValueError("Image display name mismatch")
    result = {
        "models": len(mid_rows),
        "distinct_series": len(before["models"]),
        "trims": len(after["trims"]),
        "images": len(after["images"]),
        "all_forward_reverse_links_match": True,
        "original_parameter_values_unchanged": True,
        "image_urls_unchanged": True,
        "historical_rows_and_links_unchanged": True,
        "model_table": state["new_models"],
    }
    save_json(root / "verification.json", result)
    return result
