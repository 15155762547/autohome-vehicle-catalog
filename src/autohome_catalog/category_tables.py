"""Copy verified configuration fields into Autohome category tables, then publish safely."""

import argparse
import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import RLock

from .base_utils import read_records, set_visible, settled_fields, verify_fields, write_updates
from .feishu import Client, record_index
from .multi_select import all_options
from .storage import assert_external, save_json

INDEX = "配置索引"
FOLDER = "历史备份｜分类拆表20261019"
IDENTITY = ["车型全称", "model_id", "品牌", "车系名称", "配置名称", "来源配置ID"]
SPECIAL = {"特色配置", "选装包"}
STATE_LOCK = RLock()


def guard(config, command, args):
    if command in {
        "+table-list",
        "+field-list",
        "+record-list",
        "+view-list",
        "+view-get-visible-fields",
        "+field-search-options",
        "+base-block-list",
    }:
        return
    state = config["_categories"]

    def value(flag):
        return args[args.index(flag) + 1] if flag in args else None

    owned = set(state["tables"].values())
    if command == "+table-create" and value("--name") in state["pending_names"]:
        return
    if (
        command
        in {
            "+record-batch-create",
            "+record-batch-update",
            "+field-create",
            "+view-set-visible-fields",
            "+field-update",
        }
        and value("--table-id") in owned
    ):
        return
    if command == "+field-update" and value("--table-id") == config["tables"]["models"]:
        if value("--field-id") in state.get("model_fields", []):
            return
    if command == "+view-set-visible-fields" and value("--table-id") == config["tables"]["models"]:
        if value("--view-id") in state.get("model_views", []):
            return
    if (
        command == "+record-batch-update"
        and value("--table-id") == config["tables"]["models"]
        and state.get("model_link_repairs")
    ):
        return
    if command == "+base-block-create" and args == ["--type", "folder", "--name", FOLDER]:
        return
    if command in {"+base-block-move", "+base-block-rename"}:
        if value("--block-id") in owned | {
            t for r, t in state["source_tables"].items() if r != "models"
        }:
            return
    raise ValueError(f"Outside category migration: {command}")


def definition(field):
    if field["name"] in {"长度(mm)", "宽度(mm)", "高度(mm)"}:
        raise ValueError("User removed separate dimension columns; do not restore them")
    allowed = {
        "name",
        "type",
        "style",
        "description",
        "multiple",
        "options",
        "default_value",
        "link_table",
        "bidirectional",
        "bidirectional_link_field_name",
    }
    return {k: copy.deepcopy(v) for k, v in field.items() if k in allowed and v is not None}


def link(name, table, reverse=None):
    result = {"name": name, "type": "link", "link_table": table, "bidirectional": bool(reverse)}
    if reverse:
        result["bidirectional_link_field_name"] = reverse
    return result


def load(root):
    meta = json.loads((root / "metadata.json").read_text())
    rows = {
        role: record_index(
            json.loads((root / f"{role}.json").read_text()),
            "model_id" if role == "models" else "明细ID" if role == "special" else "来源配置ID",
        )
        for role in meta["roles"]
    }
    if rows["trims"].keys() != rows["equipment"].keys():
        raise ValueError("Configuration key sets differ before category migration")
    return meta, rows


def schemas(client, root, meta, mapping):
    out = {}
    for role in ["trims", "equipment", "special"]:
        out[role] = {}
        for field in meta["roles"][role]["fields"]:
            field = copy.deepcopy(field)
            if field.get("truncated"):
                field["options"] = all_options(client, meta["roles"][role]["id"], field)
            out[role][field["name"]] = definition(field)
    save_json(root / "complete-schemas.json", out)
    return out


def fill(client, root, state, name, fields, expected, key, hidden=()):
    pending_name = mapping_name(name) + "（分类整理中）"
    with STATE_LOCK:
        if name not in state["tables"]:
            tables = client.call("+table-list")["tables"]
            matches = [t for t in tables if t["name"] == pending_name]
            if not matches:
                client.call(
                    "+table-create",
                    ["--name", pending_name, "--fields", json.dumps(fields, ensure_ascii=False)],
                )
                matches = [
                    t for t in client.call("+table-list")["tables"] if t["name"] == pending_name
                ]
            if len(matches) != 1:
                raise ValueError("Cannot identify newly created category table")
            state["tables"][name] = matches[0]["id"]
            save_json(root / "state.json", state)
    tid = state["tables"][name]
    actual = record_index(read_records(client, tid, key), key)
    if actual.keys() - expected.keys():
        raise ValueError("Unexpected records in category staging table")
    for ident, row in actual.items():
        verify_fields(expected[ident], row["fields"])
    todo = [f for ident, f in expected.items() if ident not in actual]
    for start in range(0, len(todo), 200):
        client.call(
            "+record-batch-create",
            ["--table-id", tid],
            {"create_records": todo[start : start + 200]},
        )
        print(
            json.dumps(
                {
                    "table": name,
                    "written": len(actual) + min(start + 200, len(todo)),
                    "total": len(expected),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    verified = record_index(read_records(client, tid, key), key) if todo else actual
    if verified.keys() != expected.keys():
        raise ValueError("Missing category rows on read-back")
    for ident, values in expected.items():
        verify_fields(values, verified[ident]["fields"])
    native = client.call("+field-list", ["--table-id", tid])["fields"]
    for wanted in fields:
        found = next(f for f in native if f["name"] == wanted["name"])
        if wanted["type"] == "select" and found.get("truncated"):
            found["options"] = all_options(client, tid, found)
        for attr in ["type", "multiple", "style", "options"]:
            if attr in wanted and found.get(attr) != wanted[attr]:
                raise ValueError(f"Category schema differs: {name}/{wanted['name']}/{attr}")
    views = client.call("+view-list", ["--table-id", tid])["views"]
    if len(views) != 1:
        raise ValueError("Category must have exactly one default view")
    # The view endpoint rejects concurrent writes even when the tables differ.
    with STATE_LOCK:
        args = ["--table-id", tid, "--view-id", views[0]["id"]]
        visible = [f["name"] for f in fields if f["name"] not in hidden]
        if client.call("+view-get-visible-fields", args)["visible_fields"] != visible:
            set_visible(client, args, visible)
            time.sleep(1)
    save_json(root / "verified" / f"{mapping_name(name)}.json", verified)
    with STATE_LOCK:
        state["verified"][name] = {"rows": len(verified), "fields": len(fields)}
        save_json(root / "state.json", state)
    print(json.dumps({"verified": name, "rows": len(verified)}, ensure_ascii=False), flush=True)
    return verified


def mapping_name(name):
    return name.replace("/", "／")


def validate_mapping(meta, mapping):
    identities = [(f["role"], f["name"]) for f in mapping["fields"]]
    if len(identities) != len(set(identities)):
        raise ValueError("Parameter mapped more than once")
    if any(f["section"] not in mapping["sections"] for f in mapping["fields"]):
        raise ValueError("Parameter category is not in the ordered category list")
    for role in ["trims", "equipment"]:
        wanted = {f["name"] for f in meta["roles"][role]["fields"]} - set(
            mapping["metadata_fields"]
        )
        actual = {f["name"] for f in mapping["fields"] if f["role"] == role}
        if actual != wanted:
            raise ValueError(f"Incomplete parameter mapping: {role}")


def run(config, root, publish=False):
    root = assert_external(root)
    meta, source = load(root)
    mapping = json.loads((root / "field-mapping.json").read_text())
    validate_mapping(meta, mapping)
    names = [INDEX, *mapping["sections"]]
    state_path = root / "state.json"
    state = (
        json.loads(state_path.read_text())
        if state_path.exists()
        else {
            "tables": {},
            "verified": {},
            "pending_names": [mapping_name(n) + "（分类整理中）" for n in names],
            "source_tables": {r: t["id"] for r, t in meta["roles"].items()},
            "published": False,
        }
    )
    if state["published"]:
        return state["verification"]
    state["pending_names"] = [mapping_name(n) + "（分类整理中）" for n in names]
    save_json(root / "state.json", state)
    client = Client(config)
    client.config["_categories"] = state
    native = schemas(client, root, meta, mapping)
    old = native["trims"]
    index_names = IDENTITY + [
        "销售状态",
        "来源参数页",
        "来源采集时间",
        "来源车系ID",
        "来源采集URL",
        "来源快照SHA256",
        "原始销售状态码",
        "销售状态依据",
        "选配价格说明",
    ]
    index_fields = [old[n] for n in index_names if n in old]
    index_fields += [
        link("所属车型", client.config["tables"]["models"]),
        link("原始参数存档", meta["roles"]["trims"]["id"]),
    ]
    expected = {
        key: {
            **{
                f["name"]: row["fields"].get(f["name"]) for f in index_fields if f["type"] != "link"
            },
            "所属车型": row["fields"]["所属车型"],
            "原始参数存档": [{"id": row["record_id"]}],
        }
        for key, row in source["trims"].items()
    }
    index_rows = fill(
        client,
        root,
        state,
        INDEX,
        index_fields,
        expected,
        "来源配置ID",
        hidden=["来源采集URL", "来源快照SHA256", "原始销售状态码", "销售状态依据", "原始参数存档"],
    )

    def copy_section(section):
        section_client = Client(config)
        section_client.config["_categories"] = state
        items = sorted(
            [r for r in mapping["fields"] if r["section"] == section],
            key=lambda r: (r["order"], r["raw"]),
        )
        if section in SPECIAL:
            fields = [
                native["special"][n]
                for n in [
                    "车型全称",
                    "项目名称",
                    "类别",
                    "配置状态",
                    "内容",
                    "model_id",
                    "品牌",
                    "车系名称",
                    "配置名称",
                    "来源配置ID",
                    "明细ID",
                    "来源项目ID",
                    "参数顺序",
                    "来源参数页",
                    "来源采集时间",
                    "来源车系ID",
                    "来源快照SHA256",
                ]
                if n in native["special"]
            ]
            expected = {
                k: {f["name"]: row["fields"].get(f["name"]) for f in fields}
                for k, row in source["special"].items()
                if row["fields"]["类别"] == [section]
            }
            key_name = "明细ID"
        else:
            fields = [old[n] for n in IDENTITY] + [native[r["role"]][r["name"]] for r in items]
            expected = {
                k: {
                    **{n: row["fields"].get(n) for n in IDENTITY},
                    **{r["name"]: source[r["role"]][k]["fields"].get(r["name"]) for r in items},
                }
                for k, row in source["trims"].items()
            }
            key_name = "来源配置ID"
        if len({f["name"] for f in fields}) != len(fields) or len(fields) + 1 > 300:
            raise ValueError("Duplicate or excessive fields in category plan")
        fields.append(link("对应配置", state["tables"][INDEX], section))
        for values in expected.values():
            values["对应配置"] = [{"id": index_rows[str(values["来源配置ID"])]["record_id"]}]
        fill(
            section_client,
            root,
            state,
            section,
            fields,
            expected,
            key_name,
            hidden=[r["name"] for r in items if r["raw"]] + ["来源快照SHA256"],
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(copy_section, mapping["sections"]))
    verify_category_links(client, root, state, mapping)
    save_json(root / "verified-all.json", state["verified"])
    if publish:
        publish_tables(client, config, root, state, meta, source, mapping)
    return state["verified"]


def verify_category_links(client, root, state, mapping):
    index = record_index(read_records(client, state["tables"][INDEX], "来源配置ID"), "来源配置ID")
    for section in mapping["sections"]:
        rows = json.loads((root / "verified" / f"{mapping_name(section)}.json").read_text())
        expected = {key: set() for key in index}
        for row in rows.values():
            key = str(row["fields"]["来源配置ID"])
            if key not in index:
                raise ValueError("Category configuration does not exist in index")
            if {x["id"] for x in row["fields"]["对应配置"]} != {index[key]["record_id"]}:
                raise ValueError("Category forward link differs")
            expected[key].add(row["record_id"])
        for key, row in index.items():
            if {x["id"] for x in row["fields"].get(section) or []} != expected[key]:
                raise ValueError(f"Index category backlink differs: {section}/{key}")
    save_json(root / "verified" / f"{INDEX}.json", index)
    state["category_links_verified"] = True
    save_json(root / "state.json", state)
    print(json.dumps({"category_links_verified": True}, ensure_ascii=False), flush=True)


def rename_field(client, tid, field, *, name):
    body = definition(field)
    body["name"] = name
    client.call("+field-update", ["--table-id", tid, "--field-id", field["id"], "--yes"], body)


def ensure_model_link(client, root, state, model_tid):
    tid = state["tables"][INDEX]
    fields = client.call("+field-list", ["--table-id", tid])["fields"]
    candidates = [f for f in fields if f.get("link_table") == model_tid and f.get("bidirectional")]
    if not candidates:
        client.call(
            "+field-create",
            ["--table-id", tid],
            link("所属车型（双向）", model_tid, "配置入口（分类）"),
        )
        fields = settled_fields(
            client,
            tid,
            lambda fs: any(f.get("link_table") == model_tid and f.get("bidirectional") for f in fs),
        )
        candidates = [
            f for f in fields if f.get("link_table") == model_tid and f.get("bidirectional")
        ]
    if len(candidates) != 1:
        raise ValueError("Cannot identify the new model backlink field")
    parent = candidates[0]
    expected = index_rows_from_file(root)
    current = record_index(read_records(client, tid, "来源配置ID"), "来源配置ID")
    if current.keys() != expected.keys():
        raise ValueError("Configuration index changed before model link migration")
    updates = {}
    for key, row in current.items():
        if row["record_id"] != expected[key]["record_id"]:
            raise ValueError("Configuration index record identity changed")
        wanted = expected[key]["fields"]["所属车型"]
        actual = row["fields"].get(parent["name"])
        if actual:
            verify_fields({parent["name"]: wanted}, row["fields"])
        else:
            updates[row["record_id"]] = {parent["name"]: wanted}
    write_updates(client, tid, updates)
    after = (
        record_index(read_records(client, tid, "来源配置ID"), "来源配置ID") if updates else current
    )
    if after.keys() != expected.keys():
        raise ValueError("Configuration index changed after model link migration")
    for key, row in after.items():
        if row["record_id"] != expected[key]["record_id"]:
            raise ValueError("Configuration index record identity changed after link migration")
        verify_fields({parent["name"]: expected[key]["fields"]["所属车型"]}, row["fields"])
    # Link direction is immutable: preserve the original one-way column instead of converting it.
    if parent["name"] != "所属车型":
        original = next(
            f for f in fields if f.get("link_table") == model_tid and not f.get("bidirectional")
        )
        if original["name"] != "原所属车型（单向存档）":
            rename_field(client, tid, original, name="原所属车型（单向存档）")
        rename_field(client, tid, parent, name="所属车型")


def publish_tables(client, config_path, root, state, meta, source, mapping):
    # Verify originals before replacing any entry point. New backlinks are confined to the new index.
    if not state.get("originals_checked"):
        for role in ["models", "trims", "equipment", "special"]:
            rows = record_index(
                read_records(
                    client,
                    meta["roles"][role]["id"],
                    "model_id"
                    if role == "models"
                    else "明细ID"
                    if role == "special"
                    else "来源配置ID",
                ),
                "model_id" if role == "models" else "明细ID" if role == "special" else "来源配置ID",
            )
            if rows.keys() != source[role].keys():
                raise ValueError("Original table keys changed during staging")
            for k, row in rows.items():
                if row["record_id"] != source[role][k]["record_id"]:
                    raise ValueError("Original record ID changed")
                verify_fields(source[role][k]["fields"], row["fields"])
        state["originals_checked"] = True
        save_json(root / "state.json", state)
    tid = state["tables"][INDEX]
    model_tid = meta["roles"]["models"]["id"]
    ensure_model_link(client, root, state, model_tid)
    model_schema = client.call("+field-list", ["--table-id", model_tid])["fields"]
    old_id = next(f["id"] for f in meta["roles"]["models"]["fields"] if f["name"] == "配置版本")
    old = next(f for f in model_schema if f["id"] == old_id)
    new = next(f for f in model_schema if f.get("link_table") == tid)
    state["model_fields"] = [old["id"], new["id"]]
    state["model_views"] = list(meta["roles"]["models"]["visible"])
    save_json(root / "state.json", state)
    if old["name"] != "历史配置版本（分类前）":
        rename_field(client, model_tid, old, name="历史配置版本（分类前）")
    if new["name"] != "配置版本":
        rename_field(client, model_tid, new, name="配置版本")
    model_after = record_index(read_records(client, model_tid, "model_id"), "model_id")
    index_rows = index_rows_from_file(root)
    repairs = {}
    memberships = {}
    for mid, row in source["models"].items():
        original = dict(row["fields"])
        original["历史配置版本（分类前）"] = original.pop("配置版本")
        verify_fields(original, model_after[mid]["fields"])
        expected_ids = {
            r["record_id"] for r in index_rows.values() if r["fields"]["model_id"] == mid
        }
        memberships[mid] = expected_ids
        actual_ids = {x["id"] for x in model_after[mid]["fields"].get("配置版本") or []}
        if actual_ids - expected_ids:
            raise ValueError(
                "Unexpected configuration in model backlink; do not remove it automatically"
            )
        if expected_ids != actual_ids:
            repairs[model_after[mid]["record_id"]] = {
                new["id"]: [{"id": ident} for ident in sorted(expected_ids)]
            }
    if repairs:
        # Reconcile only missing reverse links, using the already verified configuration identities.
        save_json(root / "model-link-repairs.json", repairs)
        state["model_link_repairs"] = list(repairs)
        save_json(root / "state.json", state)
        write_updates(client, model_tid, repairs)
        state.pop("model_link_repairs")
        save_json(root / "state.json", state)
        model_after = record_index(read_records(client, model_tid, "model_id"), "model_id")
        for mid, expected_ids in memberships.items():
            actual_ids = {x["id"] for x in model_after[mid]["fields"].get("配置版本") or []}
            if actual_ids != expected_ids:
                raise ValueError("Model backlink differs after bounded repair")
        index_after = record_index(read_records(client, tid, "来源配置ID"), "来源配置ID")
        if index_after.keys() != index_rows.keys():
            raise ValueError("Configuration set changed after backlink repair")
        for key, row in index_after.items():
            if row["record_id"] != index_rows[key]["record_id"]:
                raise ValueError("Configuration identity changed after backlink repair")
            verify_fields(index_rows[key]["fields"], row["fields"])
        save_json(root / "verified" / f"{INDEX}.json", index_after)
    for view in meta["roles"]["models"]["views"]["views"]:
        visible = meta["roles"]["models"]["visible"][view["id"]]["visible_fields"]
        set_visible(client, ["--table-id", model_tid, "--view-id", view["id"]], visible)
    for name, table in state["tables"].items():
        client.call("+base-block-rename", ["--block-id", table, "--name", mapping_name(name)])
    blocks = client.call("+base-block-list").get("blocks", [])
    folders = [b for b in blocks if b.get("name") == FOLDER]
    if not folders:
        client.call("+base-block-create", ["--type", "folder", "--name", FOLDER])
        folders = [
            b for b in client.call("+base-block-list").get("blocks", []) if b.get("name") == FOLDER
        ]
    if len(folders) != 1:
        raise ValueError("Archive folder cannot be identified")
    for role in ["trims", "equipment", "special"]:
        old_tid = meta["roles"][role]["id"]
        client.call("+base-block-move", ["--block-id", old_tid, "--parent-id", folders[0]["id"]])
    previous = model_tid
    for name in [INDEX, *mapping["sections"]]:
        table = state["tables"][name]
        client.call("+base-block-move", ["--block-id", table, "--after-id", previous])
        previous = table
    # Index entry points follow the same category order; technical provenance stays hidden.
    index_fields = client.call("+field-list", ["--table-id", tid])["fields"]
    state["verified"][INDEX]["fields"] = len(index_fields)
    front = (
        IDENTITY
        + ["销售状态", "所属车型"]
        + mapping["sections"]
        + ["来源参数页", "来源采集时间", "选配价格说明"]
    )
    present = {f["name"] for f in index_fields}
    view = client.call("+view-list", ["--table-id", tid])["views"][0]
    set_visible(
        client, ["--table-id", tid, "--view-id", view["id"]], [n for n in front if n in present]
    )
    saved = json.loads(Path(config_path).read_text())
    saved["archived_category_sources"] = state["source_tables"]
    saved["tables"] = {"models": model_tid, "trims": tid, "special": state["tables"]["特色配置"]}
    saved["category_tables"] = {n: t for n, t in state["tables"].items() if n != INDEX}
    saved["layout_version"] = 3
    state["verification"] = {
        "category_tables": len(mapping["sections"]),
        "index_rows": 6586,
        "source_parameter_fields": len(mapping["fields"]),
        "tables": state["verified"],
        "original_records_preserved": True,
        "model_links_verified": True,
        "category_links_verified": state["category_links_verified"],
    }
    save_json(root / "verification.json", state["verification"])
    save_json(Path(config_path), saved)
    state["published"] = True
    save_json(root / "state.json", state)


def index_rows_from_file(root):
    return json.loads((root / "verified" / f"{INDEX}.json").read_text())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.config, args.run, args.publish), ensure_ascii=False))
