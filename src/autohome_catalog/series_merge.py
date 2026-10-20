"""Merge accepted powertrain rows into source series without rebuilding configurations."""

import argparse
import hashlib
import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from .base_utils import set_visible, settled_fields, write_updates
from .feishu import Client, same_value
from .image_sync import url_lines
from .storage import assert_external, assert_run_allowed, save_json

POWER = {"BEV": "纯电动", "PHEV": "插电式混合动力", "EREV": "增程式", "FCEV": "氢燃料"}


def visible_fields(client, args, visible):
    try:
        return set_visible(client, args, visible)
    except ValueError as error:
        if str(error) != "Visible parameter order differs after bounded read-back":
            raise
        actual = client.call("+view-get-visible-fields", args)["visible_fields"]
        # A type conversion can change the service's relative column placement.
        # Keep the same visible set; never hide extra columns to force an order.
        right = [n for n in visible if n in {"品牌", "车系名称", "配置名称", "来源配置ID"}]
        if set(actual) != set(visible) or actual[0] != visible[0]:
            raise
        if "来源配置ID" in right and actual[-len(right) :] != right:
            raise
        return actual


def guard(config, command, args):
    state = config["_series_merge"]
    tid = args[args.index("--table-id") + 1] if "--table-id" in args else None
    if command in {
        "+table-list",
        "+table-get",
        "+field-list",
        "+view-list",
        "+view-get-visible-fields",
    }:
        return
    if command == "+record-list" and tid in state["tables"]:
        return
    if tid in state["tables"] and command in {
        "+field-create",
        "+field-update",
        "+field-delete",
        "+record-batch-update",
        "+view-set-visible-fields",
    }:
        return
    if command == "+record-delete" and tid == config["tables"]["models"]:
        ids = [args[i + 1] for i, value in enumerate(args) if value == "--record-id"]
        if ids and set(ids) <= set(state.get("delete_ids", [])):
            return
    raise ValueError(f"Outside source-series migration: {command}")


def export_rows(client, tid, root, label, fields=()):
    for attempt in range(4):
        try:
            return _export_rows_once(client, tid, root, label, fields)
        except ValueError as error:
            if not str(error).startswith("Table changed during snapshot:") or attempt == 3:
                raise
            time.sleep(2**attempt)
        except RuntimeError:
            if (
                client.last_failure.get("message")
                != "table schema changed during download; this request failed"
                or attempt == 3
            ):
                raise
            time.sleep(2**attempt)


def _export_rows_once(client, tid, root, label, fields):
    """Use native NDJSON pages; validate artifact identity, count and table revision."""
    rows, offset, revision = [], 0, None
    folder = client.config_path.parent / root.name / label / tid
    folder.mkdir(parents=True, exist_ok=True)
    while True:
        output = folder / f"{offset}.ndjson"
        args = [
            "--table-id",
            tid,
            "--limit",
            "2000",
            "--offset",
            str(offset),
            "--format",
            "ndjson",
            "--output",
            str(output.relative_to(client.config_path.parent)),
            "--overwrite",
        ]
        for field in fields:
            args += ["--field-id", field]
        packet = client.call("+record-list", args)
        if revision is not None and revision != packet["rev"]:
            raise ValueError(f"Table changed during snapshot: {tid}")
        revision = packet["rev"]
        if Path(packet["record_file"]).resolve() != output.resolve():
            raise ValueError("Unexpected NDJSON artifact path")
        page = [json.loads(line) for line in output.read_text().splitlines()]
        if len(page) != packet["records_count"]:
            raise ValueError("NDJSON count differs")
        rows += [
            {
                "record_id": row["record_id"],
                "fields": {k: v for k, v in row.items() if k != "record_id"},
            }
            for row in page
        ]
        if not packet["has_more"]:
            break
        if not page or packet["next_offset"] != offset + len(page):
            raise ValueError("Invalid export pagination")
        offset = packet["next_offset"]
    if len({row["record_id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate record IDs in snapshot")
    return rows


def compile_models(models, trims):
    groups, old_to_new, updates = defaultdict(list), {}, {}
    for row in models:
        groups[str(row["fields"]["来源车系ID"])].append(row)
    trim_by_mid = defaultdict(list)
    for row in trims:
        trim_by_mid[row["fields"]["model_id"]].append(row)
    for sid, members in groups.items():
        members.sort(key=lambda row: row["fields"]["model_id"])
        survivor = members[0]
        names = {row["fields"]["原车系名称"] for row in members}
        if len(names) != 1:
            raise ValueError(f"Source series names disagree: {sid}")
        images, image_groups, powers = [], [], []
        linked = set()
        cover_identity = set()
        for row in members:
            fields = row["fields"]
            mid = fields["model_id"]
            parts = mid.split("-")
            if len(parts) != 3 or parts[:2] != ["AUT", sid] or parts[2] not in POWER:
                raise ValueError(f"Invalid old powertrain identity: {mid}")
            power = parts[2]
            if fields["动力类型"] != POWER[power] or power in powers:
                raise ValueError(f"Powertrain identity disagrees: {mid}")
            powers.append(power)
            urls = url_lines(fields.get("图片URL清单（json字符串）"))
            if len(urls) != fields["图片数"]:
                raise ValueError(f"Image count differs: {mid}")
            images.extend(urls)
            image_groups.append(
                {
                    "动力类型": power,
                    "年款": fields.get("图片年款"),
                    "图片URL": urls,
                    "说明": fields.get("图片说明"),
                    "来源页": fields.get("图片采集来源页"),
                    "检查时间": fields.get("图片检查时间"),
                    "图片类别": fields.get("图片类别"),
                    "来源快照SHA256": fields.get("来源快照SHA256"),
                    "验证方式": fields.get("图片验证方式"),
                }
            )
            cover = fields.get("brochure_cover") or []
            if len(cover) != 1:
                raise ValueError(f"Expected exactly one cover: {mid}")
            cover_identity.add((cover[0]["name"].rsplit("-", 1)[1], cover[0]["size"]))
            expected = {r["record_id"] for r in trim_by_mid[mid]}
            if expected != {v["id"] for v in fields.get("配置版本") or []}:
                raise ValueError(f"Reverse configuration membership differs: {mid}")
            for trim in trim_by_mid[mid]:
                tf = trim["fields"]
                if str(tf["来源车系ID"]) != sid or tf["所属车型"] != [{"id": row["record_id"]}]:
                    raise ValueError("Configuration parent identity differs")
            linked.update(expected)
            old_to_new[row["record_id"]] = survivor["record_id"]
        if len(cover_identity) != 1:
            raise ValueError(f"Series covers differ: {sid}")
        images = list(dict.fromkeys(images))
        updates[survivor["record_id"]] = {
            "车系名称": names.pop(),
            "动力类型": powers,
            "图片URL清单（json字符串）": json.dumps(images, ensure_ascii=False),
            "图片数": len(images),
            "图库分组JSON": json.dumps(image_groups, ensure_ascii=False),
        }
        if len(members) > 1:
            updates[survivor["record_id"]].update(
                图片说明="车系图库为各动力图片的去重并集；具体动力、年款、图片来源及原说明见图库分组JSON。",
                图片年款="分动力年款见图库分组JSON",
                图片类别="、".join(
                    dict.fromkeys(r["fields"].get("图片类别") or "未标注" for r in members)
                ),
                图片验证方式="各动力组分别验证，详见图库分组JSON",
            )
    if set(trim_by_mid) != {row["fields"]["model_id"] for row in models}:
        raise ValueError("Orphan or empty model configuration group")
    return updates, old_to_new


def remap_links(value, mapping):
    if not value:
        return value
    return [{"id": rid} for rid in dict.fromkeys(mapping[v["id"]] for v in value)]


def backup(config, root):
    client = Client(config)
    tables = client.call("+table-list")["tables"]
    client.config["_series_merge"] = {"tables": [t["id"] for t in tables]}
    active = {config_id for config_id in client.config["category_tables"].values()} | set(
        client.config["tables"].values()
    )
    model_tid = client.config["tables"]["models"]

    def schema(table):
        tid = table["id"]
        fields = client.call("+field-list", ["--table-id", tid])["fields"]
        inbound = [
            f["name"] for f in fields if f["type"] == "link" and f.get("link_table") == model_tid
        ]
        if tid not in active and not inbound:
            return None
        views = client.call("+view-list", ["--table-id", tid])["views"]
        for view in views:
            view["visible"] = client.call(
                "+view-get-visible-fields", ["--table-id", tid, "--view-id", view["id"]]
            )["visible_fields"]
        return {
            "id": tid,
            "name": table["name"],
            "fields": fields,
            "views": views,
            "active": tid in active,
            "inbound": inbound,
        }

    with ThreadPoolExecutor(max_workers=3) as pool:
        metadata = [s for s in pool.map(schema, tables) if s]
    save_json(root / "config-before.json", client.config | {"_series_merge": None})
    save_json(root / "metadata.json", metadata)

    def snapshot(meta):
        path = root / "before" / f"{meta['id']}.json"
        if not path.exists():
            save_json(path, export_rows(client, meta["id"], root, "backup"))
        print(
            json.dumps(
                {"backup": meta["name"], "rows": len(json.loads(path.read_text()))},
                ensure_ascii=False,
            ),
            flush=True,
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(snapshot, metadata))
    save_json(
        root / "backup-sha256.json",
        {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (root / "before").glob("*.json")
        },
    )
    return metadata


def apply(config, root):
    root = assert_external(root)
    root.mkdir(exist_ok=True)
    if (root / "verification.json").exists():
        return json.loads((root / "verification.json").read_text())
    metadata = (
        json.loads((root / "metadata.json").read_text())
        if (root / "backup-sha256.json").exists()
        else backup(config, root)
    )
    before = {
        m["id"]: json.loads((root / "before" / f"{m['id']}.json").read_text()) for m in metadata
    }
    client = Client(config)
    mt, tt = [client.config["tables"][role] for role in ("models", "trims")]
    models, trims = before[mt], before[tt]
    if len(models) != 1010 or len(trims) != 6586:
        raise ValueError("Accepted scope changed before source-series merge")
    patches, mapping = compile_models(models, trims)
    if len(patches) != 897:
        raise ValueError("Unexpected source-series count")
    delete_ids = [rid for rid, new in mapping.items() if rid != new]
    client.config["_series_merge"] = {"tables": list(before), "delete_ids": delete_ids}
    save_json(root / "record-map.json", mapping)
    save_json(
        root / "powertrain-map.json",
        {
            row["fields"]["model_id"]: {
                "来源车系ID": str(row["fields"]["来源车系ID"]),
                "record_id": mapping[row["record_id"]],
            }
            for row in models
        },
    )
    save_json(root / "image-groups.json", patches)
    state_path = root / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"finished": []}

    state_lock = Lock()
    delete_lock = Lock()

    def stage(name, work):
        assert_run_allowed(root)
        if name not in state["finished"]:
            work()
            with state_lock:
                state["finished"].append(name)
                save_json(state_path, state)
            print(json.dumps({"finished": name}, ensure_ascii=False), flush=True)

    def models_update():
        fields = client.call("+field-list", ["--table-id", mt])["fields"]
        if not any(f["name"] == "图库分组JSON" for f in fields):
            client.call(
                "+field-create",
                ["--table-id", mt],
                {
                    "name": "图库分组JSON",
                    "type": "text",
                    "description": "各动力的图片URL、年款、来源及说明；车系图片清单为各组去重并集。",
                },
            )
        pf = next(f for f in fields if f["name"] == "动力类型")
        if pf["type"] != "select" or not pf.get("multiple"):
            client.call(
                "+field-update",
                ["--table-id", mt, "--field-id", pf["id"], "--yes"],
                {
                    "name": "动力类型",
                    "type": "select",
                    "multiple": True,
                    "options": [{"name": v} for v in [*POWER, *POWER.values()]],
                    "description": "该源站车系已采配置包含的动力；具体配置以来源配置ID区分。",
                },
            )
        settled_fields(
            client,
            mt,
            lambda fs: (
                any(
                    f["name"] == "动力类型" and f["type"] == "select" and f.get("multiple")
                    for f in fs
                )
                and any(f["name"] == "图库分组JSON" for f in fs)
            ),
        )
        write_updates(client, mt, patches)
        client.call(
            "+field-update",
            ["--table-id", mt, "--field-id", pf["id"], "--yes"],
            {
                "name": "动力类型",
                "type": "select",
                "multiple": True,
                "options": [{"name": v} for v in POWER],
                "description": "该源站车系已采配置包含的动力；具体配置以来源配置ID区分。",
            },
        )

    def baseline():
        for tid in (mt, tt):
            current = export_rows(client, tid, root, "preflight")
            original = {r["record_id"]: r for r in before[tid]}
            if {r["record_id"] for r in current} != set(original):
                raise ValueError("Model/configuration identities changed since backup")
            for row in current:
                if any(
                    not same_value(value, row["fields"].get(name))
                    for name, value in original[row["record_id"]]["fields"].items()
                ):
                    raise ValueError("Model/configuration cells changed since backup")

    stage("baseline", baseline)
    stage("models", models_update)
    for meta in metadata:
        tid = meta["id"]
        if not meta["inbound"]:
            continue

        def reparent(meta=meta, tid=tid):
            updates = {}
            for row in before[tid]:
                patch = {
                    f: remap_links(row["fields"].get(f), mapping)
                    for f in meta["inbound"]
                    if row["fields"].get(f)
                }
                patch = {f: v for f, v in patch.items() if not same_value(v, row["fields"].get(f))}
                if patch:
                    updates[row["record_id"]] = patch
            write_updates(client, tid, updates)

        stage("reparent:" + tid, reparent)

    # Read back every inbound relation before removing duplicate parent rows.
    def verify_links():
        for meta in metadata:
            if not meta["inbound"]:
                continue
            actual = export_rows(client, meta["id"], root, "links", meta["inbound"])
            expected = {r["record_id"]: r for r in before[meta["id"]]}
            if len(actual) != len(expected):
                raise ValueError("Child row count changed")
            for row in actual:
                for f in meta["inbound"]:
                    if not same_value(
                        row["fields"].get(f),
                        remap_links(expected[row["record_id"]]["fields"].get(f), mapping),
                    ):
                        raise ValueError("Unverified parent remapping")

    stage("links-verified", verify_links)

    def remove_duplicates():
        current = export_rows(client, mt, root, "before-delete", ["来源车系ID"])
        existing = {r["record_id"] for r in current}
        for start in range(0, len(delete_ids), 50):
            ids = [rid for rid in delete_ids[start : start + 50] if rid in existing]
            if ids:
                args = ["--table-id", mt, "--yes"]
                for rid in ids:
                    args += ["--record-id", rid]
                client.call("+record-delete", args)

    stage("duplicates-removed", remove_duplicates)

    def configuration_powers():
        fields = client.call("+field-list", ["--table-id", tt])["fields"]
        if not any(f["name"] == "动力类型" for f in fields):
            client.call(
                "+field-create",
                ["--table-id", tt],
                {
                    "name": "动力类型",
                    "type": "select",
                    "multiple": False,
                    "options": [{"name": code} for code in POWER],
                    "description": "该具体配置的动力，保留原动力分组的含义；一款配置以来源配置ID唯一识别。",
                },
            )
        settled_fields(
            client,
            tt,
            lambda fs: any(f["name"] == "动力类型" and f["type"] == "select" for f in fs),
        )
        write_updates(
            client,
            tt,
            {
                r["record_id"]: {"动力类型": [r["fields"]["model_id"].rsplit("-", 1)[1]]}
                for r in trims
            },
        )

    stage("configuration-power", configuration_powers)

    def run_identity(meta):
        tid = meta["id"]

        def identity(meta=meta, tid=tid):
            fields = client.call("+field-list", ["--table-id", tid])["fields"]
            if not any(f["name"] == "来源车系ID" for f in fields):
                if not any(f["name"] == "对应配置" and f.get("link_table") == tt for f in fields):
                    raise ValueError("Cannot derive series identity from configuration link")
                client.call(
                    "+field-create",
                    ["--table-id", tid, "--i-have-read-guide"],
                    {
                        "name": "来源车系ID",
                        "type": "formula",
                        "expression": "FIRST([对应配置].[来源车系ID])",
                        "description": "从对应配置自动取得源站车系ID；一款具体配置仍以来源配置ID唯一识别。",
                    },
                )
                settled_fields(client, tid, lambda fs: any(f["name"] == "来源车系ID" for f in fs))
            # Do not remove the old identifier until every new series ID is checked.
            expected = {r["record_id"]: r for r in before[tid]}
            for attempt in range(4):
                actual = export_rows(client, tid, root, "identity", ["来源车系ID"])
                if all(row["fields"].get("来源车系ID") for row in actual):
                    break
                if attempt == 3:
                    raise ValueError("Series formula has not finished calculating; resume later")
                time.sleep(2**attempt)
            for row in actual:
                old = expected[row["record_id"]]["fields"]
                sid = str(old.get("来源车系ID") or old["model_id"].split("-")[1])
                if row["fields"].get("来源车系ID") != sid:
                    raise ValueError(
                        f"Computed series ID differs: {meta['name']}/{row['record_id']}"
                    )
            old = next((f for f in fields if f["name"] == "model_id"), None)
            if old:
                with delete_lock:
                    time.sleep(2)
                    client.call(
                        "+field-delete", ["--table-id", tid, "--field-id", old["id"], "--yes"]
                    )
            for view in meta["views"]:
                visible = list(
                    dict.fromkeys("来源车系ID" if n == "model_id" else n for n in view["visible"])
                )
                if tid == tt and "动力类型" not in visible:
                    visible.insert(1, "动力类型")
                if tid == mt and view["type"] == "grid":
                    visible = [n for n in visible if n != "来源车系ID"]
                    visible.insert(2, "来源车系ID")
                visible_fields(client, ["--table-id", tid, "--view-id", view["id"]], visible)

        stage("identity:" + tid, identity)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(run_identity, [m for m in metadata if m["active"]]))

    # Compare all remaining original cells, not only row counts or a sample.
    def verify(meta):
        tid = meta["id"]
        rows = export_rows(client, tid, root, "final")
        save_json(root / "after" / f"{tid}.json", rows)
        expected = {
            r["record_id"]: r for r in before[tid] if tid != mt or r["record_id"] not in delete_ids
        }
        if {r["record_id"] for r in rows} != set(expected):
            raise ValueError(f"Record identity set changed: {meta['name']}")
        for row in rows:
            old = expected[row["record_id"]]["fields"]
            for name, value in old.items():
                if meta["active"] and name == "model_id":
                    continue
                if tid == mt and name in patches[row["record_id"]]:
                    value = patches[row["record_id"]][name]
                elif name in meta["inbound"]:
                    value = remap_links(value, mapping)
                elif tid == mt and name in {"配置版本", "历史配置版本（分类前）"}:
                    members = [r for r in models if mapping[r["record_id"]] == row["record_id"]]
                    value = [
                        {"id": rid}
                        for rid in dict.fromkeys(
                            v["id"] for r in members for v in r["fields"].get(name) or []
                        )
                    ]
                if not same_value(row["fields"].get(name), value):
                    raise ValueError(
                        f"Unexpected cell difference: {meta['name']}/{row['record_id']}/{name}"
                    )
            if tid == mt and len(row["fields"].get("brochure_cover") or []) != 1:
                raise ValueError("Series cover count changed")
            if tid == tt and row["fields"].get("动力类型") != [old["model_id"].rsplit("-", 1)[1]]:
                raise ValueError("Configuration powertrain changed")
        print(
            json.dumps({"verified": meta["name"], "rows": len(rows)}, ensure_ascii=False),
            flush=True,
        )
        return {"table": meta["name"], "rows": len(rows)}

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(verify, metadata))
    final = json.loads(config.read_text())
    final.update(
        model_grain="series",
        key_fields={**final.get("key_fields", {}), "models": "来源车系ID"},
        expected_series_count=897,
        series_merge_run=root.name,
    )
    save_json(config, final)
    result = {
        "models": 897,
        "configurations": 6586,
        "removed_duplicate_models": len(delete_ids),
        "verified_tables": results,
        "old_id_mapping": "record-map.json",
        "ray7": "目录已确认新增预售，尚未补采，不计入本次合并数量",
    }
    save_json(root / "verification.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description="按来源车系ID合并动力车型，保留配置及图库")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(apply(args.config, args.root), ensure_ascii=False))


if __name__ == "__main__":
    main()
