"""Backed-up migration: ordinary parameters in two grids, special items in rows."""

import copy
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

from .export import EQUIPMENT_FIELDS, TRIM_FIELDS, key_of, raw_column
from .feishu import Client, record_index, same_value
from .storage import assert_external, save_json

SPECIAL = {"选装包", "特色配置"}
FIRST_SECTIONS = {
    "基本参数",
    "车身",
    "发动机",
    "电动机",
    "电池/充电",
    "变速箱",
    "底盘转向",
    "车轮制动",
    "被动安全",
    "主动安全",
}
NAMES = {"trims": "vehicle_trims", "equipment": "配置参数扩展", "special": "专属配置明细"}
PENDING = {role: name + "（整理中）" for role, name in NAMES.items()}
FOLDER = "历史备份｜参数表调整20261017"
KEYS = {"trims": "来源配置ID", "equipment": "来源配置ID", "special": "明细ID"}


def assert_migration_operation(config, command, args):
    state = config["human_layout_migration"]
    reads = {
        "+table-list",
        "+field-list",
        "+record-list",
        "+view-list",
        "+view-get-visible-fields",
        "+base-block-list",
        "+view-get-sort",
        "+view-get-filter",
        "+view-get-group",
    }
    if command in reads:
        return
    new = set(state.get("new_tables", {}).values())
    if command == "+table-create" and args[args.index("--name") + 1] in PENDING.values():
        return
    if command == "+base-block-create":
        if args == ["--type", "folder", "--name", FOLDER]:
            return
    if command == "+base-block-rename":
        bid, name = args[args.index("--block-id") + 1], args[args.index("--name") + 1]
        allowed = {
            **state["archive_names"],
            **{tid: NAMES[role] for role, tid in state.get("new_tables", {}).items()},
        }
        if allowed.get(bid) == name:
            return
    if command == "+base-block-move":
        bid = args[args.index("--block-id") + 1]
        if bid in state["archive_names"] and "--parent-id" in args:
            if args[args.index("--parent-id") + 1] == state.get("folder_id"):
                return
        if bid in new | {state.get("folder_id")} and "--parent-id" not in args:
            return
    if "--table-id" in args:
        tid = args[args.index("--table-id") + 1]
        if tid in new and command in {
            "+record-batch-create",
            "+view-create",
            "+view-set-visible-fields",
            "+view-set-sort",
            "+view-set-filter",
            "+view-set-group",
        }:
            return
        if tid == config["tables"]["models"] and command == "+view-set-visible-fields":
            return
    raise ValueError(f"Operation outside the backed-up layout migration: {command}")


def detail_key(spec, field):
    identity = json.dumps(key_of(field), ensure_ascii=False, separators=(",", ":"))
    return str(spec) + ":" + hashlib.sha256(identity.encode()).hexdigest()[:20]


def field_definition(name, kind="text", description=""):
    field = {"name": name, "type": kind}
    if description:
        field["description"] = description
    if kind == "number":
        field["style"] = {"type": "plain", "precision": 4}
    if name == "来源采集时间":
        field.update(type="datetime", style={"format": "yyyy-MM-dd HH:mm"})
    if name in {"来源参数页", "来源采集URL"}:
        field["style"] = {"type": "url"}
    if name in {"销售状态", "类别"}:
        options = ["在售", "预售"] if name == "销售状态" else ["特色配置", "选装包"]
        field.update(type="select", multiple=False, options=[{"name": x} for x in options])
    return field


def set_visible(client, args, visible):
    visible = list(dict.fromkeys(visible))
    client.call("+view-set-visible-fields", args, body={"visible_fields": visible})
    for attempt in range(5):
        actual = client.call("+view-get-visible-fields", args)["visible_fields"]
        if actual == visible:
            return actual
        time.sleep(0.5 * (attempt + 1))
    raise ValueError("Visible parameter order differs after bounded read-back")


def build_layout(source: Path, migration: Path):
    """Use verified live values, preserving edits and all numeric source text."""
    manifest = json.loads((migration / "backup-verification.json").read_text())
    if not manifest["revisions_unchanged"]:
        raise ValueError("A revision-verified backup is required")
    live = {}
    for table in manifest["tables"]:
        path = migration / "backup" / table["role"] / "records.json"
        if hashlib.sha256(path.read_bytes()).hexdigest() != table["sha256"]:
            raise ValueError("Backup hash changed")
        key = "来源车系ID" if table["role"] == "models" else "来源配置ID"
        live[table["role"]] = record_index(json.loads(path.read_text()), key)
    catalog = json.loads((source / "full-field-catalog.json").read_text())
    definitions = {}
    for batch in ("main", "extension2", "extension3", "extension4", "extension5"):
        fs = json.loads((source / "feishu-batches" / batch / "fields.json").read_text())["fields"]
        for f in fs:
            role = f["table"] if batch == "main" else batch
            definitions[key_of(f)] = (f, role)
    ordered = [definitions[key_of(f)] for f in catalog]
    ordinary = [(f, role) for f, role in ordered if f["section"] not in SPECIAL]
    conditional = set()
    for f, role in ordinary:
        if f["kind"] == "number" and any(
            row["fields"].get(f["column"]) is None
            and row["fields"].get(raw_column(f)) not in (None, "", "-")
            for row in live[role].values()
        ):
            conditional.add(f["column"])
    schemas = {
        "trims": [field_definition(n) for n in TRIM_FIELDS + ["车系名称", "品牌", "参数原文存档"]],
        "equipment": [
            field_definition(n) for n in EQUIPMENT_FIELDS + ["车系名称", "品牌", "来源车系ID"]
        ],
    }
    schemas["trims"].append(
        {
            "name": "所属车系",
            "type": "link",
            "link_table": "@models",
            "bidirectional": True,
            "bidirectional_link_field_name": "配置版本",
        }
    )
    schemas["equipment"].append(
        {
            "name": "对应配置",
            "type": "link",
            "link_table": "@trims",
            "bidirectional": True,
            "bidirectional_link_field_name": "参数扩展",
        }
    )
    schemas["special"] = [
        field_definition(n, kind)
        for n, kind in [
            ("项目名称", "text"),
            ("model_id", "text"),
            ("车系名称", "text"),
            ("品牌", "text"),
            ("配置名称", "text"),
            ("来源配置ID", "text"),
            ("类别", "text"),
            ("内容", "text"),
            ("参数顺序", "number"),
            ("明细ID", "text"),
            ("来源项目ID", "text"),
            ("来源参数页", "text"),
            ("来源采集时间", "text"),
            ("来源快照SHA256", "text"),
            ("来源车系ID", "text"),
        ]
    ]
    schemas["special"].append(
        {
            "name": "对应配置",
            "type": "link",
            "link_table": "@trims",
            "bidirectional": True,
            "bidirectional_link_field_name": "专属配置",
        }
    )
    columns = {"trims": [], "equipment": []}
    for f, _ in ordinary:
        role = "trims" if f["section"] in FIRST_SECTIONS else "equipment"
        description = (
            f"汽车之家{f['group']}/{f['section']}；原始参数ID={f['item_id']}；"
            f"原名={f['name']}。完整符号和条件保留，不把缺失当不支持。"
        )
        schemas[role].append(field_definition(f["column"], f["kind"], description))
        columns[role].append(f["column"])
        if f["column"] in conditional:
            schemas[role].append(
                field_definition(raw_column(f), description="非单值数值的完整条件原文")
            )
            columns[role].append(raw_column(f))
    if len(schemas["trims"]) + 2 > 300 or any(len(s) > 300 for s in schemas.values()):
        raise ValueError("Human layout exceeds the physical 300-column limit")
    for schema in schemas.values():
        if len({f["name"] for f in schema}) != len(schema):
            raise ValueError("Duplicate human column name")
    rows = {role: [] for role in schemas}
    lineage = Counter()
    for spec, old_trim in live["trims"].items():
        old = old_trim["fields"]
        sid = str(old["来源车系ID"])
        model = live["models"][sid]["fields"]
        human = {k: old.get(k) for k in TRIM_FIELDS}
        identity = {"车系名称": model["车系名称"], "品牌": model["品牌"]}
        human.update(identity)
        equipment = {k: live["equipment"][spec]["fields"].get(k) for k in EQUIPMENT_FIELDS}
        equipment.update(identity, 来源车系ID=sid)
        raw = {}
        for f, old_role in ordinary:
            values = live[old_role].get(spec, {}).get("fields", {})
            target = human if f["section"] in FIRST_SECTIONS else equipment
            target[f["column"]] = values.get(f["column"])
            if f["kind"] == "number":
                raw[f["column"]] = values.get(raw_column(f))
                if f["column"] in conditional:
                    target[raw_column(f)] = values.get(raw_column(f))
            lineage["ordinary_nonempty"] += values.get(f["column"]) not in (None, "")
        human["参数原文存档"] = json.dumps(raw, ensure_ascii=False)
        rows["trims"].append({"key": spec, "parent": sid, "fields": human})
        rows["equipment"].append({"key": spec, "parent": spec, "fields": equipment})
        for order, (f, old_role) in enumerate(ordered, 1):
            if f["section"] not in SPECIAL:
                continue
            old_values = live[old_role].get(spec, {}).get("fields", {})
            value = old_values.get(f["column"])
            if value in (None, ""):
                continue
            fields = {k: old_values.get(k, old.get(k)) for k in EQUIPMENT_FIELDS}
            fields.update(
                identity,
                项目名称=f["name"].strip(),
                类别=[f["section"]],
                内容=value,
                参数顺序=order,
                明细ID=detail_key(spec, f),
                来源项目ID=str(f["item_id"]),
                来源车系ID=sid,
            )
            rows["special"].append({"key": fields["明细ID"], "parent": spec, "fields": fields})
            lineage["special_nonempty"] += 1
    for role, data in rows.items():
        if len({r["key"] for r in data}) != len(data):
            raise ValueError("Duplicate layout key")
    visible = {
        "trims": [
            "配置名称",
            "model_id",
            "车系名称",
            "品牌",
            "来源配置ID",
            "销售状态",
            "所属车系",
            "参数扩展",
            "专属配置",
        ]
        + columns["trims"]
        + ["来源参数页"],
        "equipment": ["配置名称", "model_id", "车系名称", "品牌", "来源配置ID", "对应配置"]
        + columns["equipment"]
        + ["来源参数页"],
        "special": [
            "项目名称",
            "model_id",
            "车系名称",
            "配置名称",
            "类别",
            "内容",
            "对应配置",
            "品牌",
            "来源配置ID",
            "来源参数页",
        ],
    }
    result = {
        "schemas": schemas,
        "rows": rows,
        "visible": visible,
        "lineage": dict(lineage),
        "conditional_columns": sorted(conditional),
        "source_fields": len(catalog),
    }
    save_json(migration / "layout-plan.json", result)
    return result


def read_records(client, table_id, key):
    for attempt in range(4):
        try:
            return _read_records_once(client, table_id, key)
        except ValueError as error:
            if str(error) != "Table changed during layout read-back" or attempt == 3:
                raise
            time.sleep(min(2**attempt, 4))


def _read_records_once(client, table_id, key):
    rows, offset, revision = [], 0, None
    while True:
        data = client.call(
            "+record-list", ["--table-id", table_id, "--limit", "200", "--offset", str(offset)]
        )
        if revision is not None and revision != data["rev"]:
            raise ValueError("Table changed during layout read-back")
        revision = data["rev"]
        rows.extend(
            {"record_id": rid, "fields": dict(zip(data["fields"], values, strict=True))}
            for rid, values in zip(data["record_id_list"], data["data"], strict=True)
        )
        if not data["has_more"]:
            record_index(rows, key)
            return rows
        if not data["data"]:
            raise ValueError("Empty page before end")
        offset += len(data["data"])


def migrate(config_path: Path, source: Path, migration: Path):
    """Create and verify replacements before archiving any old parameter table."""
    source, migration = assert_external(source), assert_external(migration)
    plan = build_layout(source, migration)
    path = migration / "migration-config.json"
    if not path.exists():
        config = json.loads(config_path.read_text())
        old = json.loads((migration / "tables-before.json").read_text())
        ids = {
            config["tables"]["trims"],
            config["tables"]["equipment"],
            *config["extension_tables"].values(),
        }
        config["human_layout_migration"] = {
            "archive_names": {
                t["id"]: t["name"] + "（横向旧版20261017）" for t in old if t["id"] in ids
            },
            "new_tables": {},
        }
        save_json(path, config)
    client = Client(path)
    state = client.config["human_layout_migration"]
    # Before the first write, detect edits after the live backup.
    if not state["new_tables"]:
        current = {t["id"]: t for t in client.call("+table-list")["tables"]}
        backup = json.loads((migration / "backup-verification.json").read_text())
        if any(current[t["id"]]["rev"] != t["rev"] for t in backup["tables"]):
            raise ValueError("Live tables changed after backup")
    model_rows = json.loads((migration / "backup/models/records.json").read_text())
    parent_ids = {k: v["record_id"] for k, v in record_index(model_rows, "来源车系ID").items()}
    expected = {}
    for role in ("trims", "equipment", "special"):
        if role not in state["new_tables"]:
            schema = copy.deepcopy(plan["schemas"][role])
            for field in schema:
                if field.get("link_table") == "@models":
                    field["link_table"] = client.config["tables"]["models"]
                elif field.get("link_table") == "@trims":
                    field["link_table"] = state["new_tables"]["trims"]
            args = [
                "--name",
                PENDING[role],
                "--fields",
                json.dumps(schema, ensure_ascii=False),
                "--view",
                json.dumps({"name": "按汽车之家顺序", "type": "grid"}),
            ]
            existing = [
                t for t in client.call("+table-list")["tables"] if t["name"] == PENDING[role]
            ]
            if existing:
                raise ValueError("Unregistered pending table; inspect creation receipt")
            client.call("+table-create", args, dry_run=True)
            client.call("+table-create", args)
            found = [t for t in client.call("+table-list")["tables"] if t["name"] == PENDING[role]]
            if len(found) != 1:
                raise ValueError("Creation not yet visible; inspect receipt before resuming")
            state["new_tables"][role] = found[0]["id"]
            save_json(path, client.config)
        tid = state["new_tables"][role]
        actual_schema = client.call("+field-list", ["--table-id", tid])["fields"]
        if not {f["name"] for f in plan["schemas"][role]} <= {f["name"] for f in actual_schema}:
            raise ValueError("Created schema incomplete")
        current = record_index(read_records(client, tid, KEYS[role]), KEYS[role])
        resolved = []
        for row in plan["rows"][role]:
            fields = dict(row["fields"])
            field = "所属车系" if role == "trims" else "对应配置"
            fields[field] = [{"id": parent_ids[row["parent"]]}]
            resolved.append({"key": row["key"], "fields": fields})
        expected[role] = resolved
        pending = [row for row in resolved if row["key"] not in current]
        if set(current) - {r["key"] for r in resolved}:
            raise ValueError("Unexpected records in migration table")
        for row in resolved:
            if row["key"] in current and any(
                not same_value(v, current[row["key"]]["fields"].get(k))
                for k, v in row["fields"].items()
            ):
                raise ValueError("Existing migration record differs; never overwrite silently")
        for offset in range(0, len(pending), 200):
            batch = pending[offset : offset + 200]
            response = client.call(
                "+record-batch-create",
                ["--table-id", tid],
                body={"create_records": [r["fields"] for r in batch]},
            )
            save_json(migration / "receipts" / f"{role}-{offset}.json", response)
            print(
                json.dumps(
                    {
                        "writing": role,
                        "created": min(offset + 200, len(pending)),
                        "of": len(pending),
                    }
                ),
                flush=True,
            )
        actual = read_records(client, tid, KEYS[role]) if pending else list(current.values())
        index = record_index(actual, KEYS[role])
        if set(index) != {r["key"] for r in resolved}:
            raise ValueError("Record identity/count verification failed")
        differences = [
            (r["key"], k)
            for r in resolved
            for k, v in r["fields"].items()
            if not same_value(v, index[r["key"]]["fields"].get(k))
        ]
        if differences:
            save_json(migration / "differences.json", differences)
            raise ValueError("Layout values differ after read-back")
        save_json(migration / f"verified-{role}.json", actual)
        if role == "trims":
            parent_ids = {k: v["record_id"] for k, v in index.items()}
        print(json.dumps({"verified": role, "rows": len(actual), "differences": 0}), flush=True)
    save_json(migration / "expected-resolved.json", expected)
    return client, plan


def finish(config_path: Path, migration: Path):
    """Verify both sides of links, configure search views, then switch the entry points."""
    migration = assert_external(migration)
    client = Client(migration / "migration-config.json")
    state = client.config["human_layout_migration"]
    plan = json.loads((migration / "layout-plan.json").read_text())
    tables = state["new_tables"]
    verified = {
        role: json.loads((migration / f"verified-{role}.json").read_text()) for role in NAMES
    }
    alias = copy.copy(client)
    alias.config = copy.deepcopy(client.config)
    alias.config["tables"]["trims"] = tables["trims"]
    trims = alias.records("trims", ["来源配置ID", "参数扩展", "专属配置"])
    indexes = record_index(trims, "来源配置ID")
    checked_links = 0
    for role, reverse in (("equipment", "参数扩展"), ("special", "专属配置")):
        children = {}
        for row in verified[role]:
            spec = row["fields"]["来源配置ID"]
            children.setdefault(spec, []).append({"id": row["record_id"]})
        for spec, row in indexes.items():
            if not same_value(row["fields"].get(reverse) or [], children.get(spec, [])):
                raise ValueError("Reverse configuration link verification failed")
        checked_links += len(verified[role])
    models = client.records("models", ["来源车系ID", "配置版本"])
    children = {}
    for row in verified["trims"]:
        children.setdefault(row["fields"]["来源车系ID"], []).append({"id": row["record_id"]})
    for row in models:
        if not same_value(
            row["fields"].get("配置版本") or [], children.get(row["fields"]["来源车系ID"], [])
        ):
            raise ValueError("Model-to-configuration reverse link verification failed")
    checked_links += len(verified["trims"])
    view_receipts = {}
    for role, tid in tables.items():
        views = client.call("+view-list", ["--table-id", tid])["views"]
        first = views[0]["id"]
        definitions = [
            (v["id"], None, False)
            for v in views
            if v["id"] == first or v["name"] == "按汽车之家顺序"
        ]
        extra = [("按品牌车系查找", None, True)]
        if role == "trims":
            extra += [
                ("只看在售", ("销售状态", "在售"), False),
                ("只看预售", ("销售状态", "预售"), False),
            ]
        elif role == "special":
            extra += [
                ("只看选装包", ("类别", "选装包"), False),
                ("只看特色配置", ("类别", "特色配置"), False),
            ]
        for name, condition, grouped in extra:
            existing = [v for v in views if v["name"] == name]
            if not existing:
                client.call(
                    "+view-create", ["--table-id", tid], body={"name": name, "type": "grid"}
                )
                views = client.call("+view-list", ["--table-id", tid])["views"]
                existing = [v for v in views if v["name"] == name]
            definitions.append((existing[0]["id"], condition, grouped))
        for vid, condition, grouped in definitions:
            args = ["--table-id", tid, "--view-id", vid]
            actual = set_visible(client, args, plan["visible"][role])
            sort = [{"field": n, "desc": False} for n in ("品牌", "车系名称", "配置名称")]
            if role == "special":
                sort.append({"field": "参数顺序", "desc": False})
            client.call("+view-set-sort", args, body={"sort_config": sort})
            sort_readback = client.call("+view-get-sort", args)
            if condition:
                field, value = condition
                client.call(
                    "+view-set-filter",
                    args,
                    body={"logic": "and", "conditions": [[field, "intersects", [value]]]},
                )
            if grouped:
                client.call(
                    "+view-set-group",
                    args,
                    body={
                        "group_config": [
                            {"field": "品牌", "desc": False},
                            {"field": "车系名称", "desc": False},
                        ]
                    },
                )
            view_receipts[vid] = {
                "role": role,
                "visible_fields": actual,
                "sort": sort_readback,
                "filter": client.call("+view-get-filter", args),
                "group": client.call("+view-get-group", args),
            }
    # Keep the existing model values and image fields; only point its visible link to new trims.
    model_tid = client.config["tables"]["models"]
    for view in client.call("+view-list", ["--table-id", model_tid])["views"]:
        args = ["--table-id", model_tid, "--view-id", view["id"]]
        old = client.call("+view-get-visible-fields", args)["visible_fields"]
        visible = [n for n in old if n not in {"配置列表", "配置版本"}]
        visible.insert(min(4, len(visible)), "配置版本")
        set_visible(client, args, visible)
    save_json(migration / "view-verification.json", view_receipts)
    # Old data must still have the same row counts before it is moved into the backup folder.
    before = {t["id"]: t for t in json.loads((migration / "tables-before.json").read_text())}
    live = client.call("+table-list")["tables"]
    for tid in state["archive_names"]:
        current = next(t for t in live if t["id"] == tid)
        if current["records_count"] != before[tid]["records_count"] or (
            current["rev"] != before[tid]["rev"] and current["name"] != state["archive_names"][tid]
        ):
            raise ValueError("An old parameter table changed during migration")
    blocks = client.call("+base-block-list")["blocks"]
    folder = next((b for b in blocks if b["name"] == FOLDER), None)
    if not folder:
        client.call("+base-block-create", ["--type", "folder", "--name", FOLDER])
        folder = next(b for b in client.call("+base-block-list")["blocks"] if b["name"] == FOLDER)
    state["folder_id"] = folder["id"]
    save_json(client.config_path, client.config)
    for tid, name in state["archive_names"].items():
        if next(t for t in live if t["id"] == tid)["name"] != name:
            client.call("+base-block-rename", ["--block-id", tid, "--name", name])
        client.call("+base-block-move", ["--block-id", tid, "--parent-id", folder["id"]])
    previous = model_tid
    for role in ("trims", "equipment", "special"):
        tid = tables[role]
        current = next(t for t in client.call("+table-list")["tables"] if t["id"] == tid)
        if current["name"] != NAMES[role]:
            client.call("+base-block-rename", ["--block-id", tid, "--name", NAMES[role]])
        client.call("+base-block-move", ["--block-id", tid, "--after-id", previous])
        previous = tid
    client.call("+base-block-move", ["--block-id", folder["id"], "--after-id", previous])
    config = json.loads(config_path.read_text())
    config.update(
        layout_version=2,
        tables={"models": model_tid, **tables},
        extension_tables={},
        extension_table_names=[],
        archived_parameter_tables=state["archive_names"],
    )
    save_json(config_path, config)
    result = {
        "tables": {"models": model_tid, **tables},
        "rows": {k: len(v) for k, v in verified.items()},
        "differences": 0,
        "verified_reverse_links": checked_links,
        "archived_tables": state["archive_names"],
        "folder_id": folder["id"],
        "deleted_tables": [],
        "deleted_records": [],
    }
    save_json(migration / "migration-verification.json", result)
    return result
