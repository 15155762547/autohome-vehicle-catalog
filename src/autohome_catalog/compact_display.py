"""Compact parameter text in place, preserving rows, fields, evidence and links."""

import argparse
import hashlib
import json
import time
from pathlib import Path

from .feishu import Client, same_value
from .human_layout import read_records
from .storage import assert_external, save_json

ALIASES = {
    "主座椅调节方式": "主驾调节",
    "副座椅调节方式": "副驾调节",
    "主/副驾驶座电动调节": "主/副驾电调",
    "前排座椅功能": "前排座椅",
    "第二排座椅功能": "二排座椅",
    "第三排座椅功能": "三排座椅",
    "电动座椅记忆功能": "座椅记忆",
    "多媒体/充电接口": "接口",
    "手机APP远程功能": "APP远程",
    "手机互联/映射": "手机互联",
    "语音识别控制系统": "语音控制",
    "外后视镜功能": "外镜功能",
    "车内化妆镜": "化妆镜",
    "钥匙类型": "钥匙",
    "驾驶辅助影像": "辅助影像",
    "驾驶模式切换": "驾驶模式",
}
STATUS = {"标配（●）": "●", "选配（○）": "○"}


def compact(name, value):
    if not isinstance(value, str) or name.startswith(("来源", "原文")):
        return value
    if name == "参数原文存档" or not any(s in value for s in STATUS):
        return value
    text = value
    for old, new in STATUS.items():
        text = text.replace(old, " " + new)
    # Source punctuation inside brackets belongs to a condition, not another function.
    depth = 0
    output = []
    for char in text:
        if char in "（([【":
            depth += 1
        elif char in "）)]】":
            depth = max(0, depth - 1)
        output.append("\n" if char == "；" and depth == 0 else char)
    text = "".join(output)
    replacements = []
    if name in ALIASES:
        replacements += [
            ("副驾驶位", "副驾"),
            ("主驾驶位", "主驾"),
            ("副驾驶", "副驾"),
            ("主驾驶", "主驾"),
        ]
    if name in {"主座椅调节方式", "副座椅调节方式"}:
        replacements += [
            ("前后调节", "前后"),
            ("靠背调节", "靠背"),
            ("高低调节", "高低"),
            ("腿托调节", "腿托"),
            ("腰部支撑", "腰撑"),
        ]
    if name == "外后视镜功能":
        replacements += [("电动调节", "电调"), ("后视镜加热", "加热"), ("后视镜记忆", "记忆")]
    if name == "手机互联/映射":
        replacements += [
            ("支持CarPlay", "CarPlay"),
            ("支持HUAWEI HiCar", "HUAWEI HiCar"),
            ("支持Carlink", "Carlink"),
            ("支持Flyme link", "Flyme link"),
        ]
    if name == "电动座椅记忆功能":
        replacements += [("驾驶位", "主驾")]
    for old, new in replacements:
        text = text.replace(old, new)
    return text.strip()


def guard(config, command, args):
    if command in {
        "+table-list",
        "+field-list",
        "+record-list",
        "+view-list",
        "+view-get-visible-fields",
    }:
        return
    tid = args[args.index("--table-id") + 1] if "--table-id" in args else None
    if tid in {config["tables"][r] for r in ("trims", "equipment")}:
        if command == "+record-batch-update":
            return
        if (
            command == "+field-update"
            and args[args.index("--field-id") + 1] in config["_compact_display"]["rename_ids"]
        ):
            return
    raise ValueError(f"Outside compact display operation: {command}")


def execute(config, root):
    root = assert_external(root)
    proofs = json.loads((root / "backup-verification.json").read_text())
    before_tables = json.loads((root / "tables-before.json").read_text())
    client = Client(config)
    backed_up_config = json.loads((root / "config-before.json").read_text())
    if any(client.config[k] != backed_up_config[k] for k in ("base_token", "profile", "tables")):
        raise ValueError("Target configuration differs from backup")
    client.config["_compact_display"] = {"rename_ids": []}
    plans = {}
    for role in ("trims", "equipment"):
        directory = root / role
        if (
            hashlib.sha256((directory / "records.json").read_bytes()).hexdigest()
            != proofs[role]["sha256"]
        ):
            raise ValueError("Backup hash mismatch")
        schema = json.loads((directory / "schema.json").read_text())
        rows = json.loads((directory / "records.json").read_text())
        text_names = {f["name"] for f in schema if f["type"] == "text"}
        rename = {f["name"]: ALIASES[f["name"]] for f in schema if f["name"] in ALIASES}
        if len({rename.get(f["name"], f["name"]) for f in schema}) != len(schema):
            raise ValueError("Short field names collide")
        desired = {}
        changed = 0
        for row in rows:
            fields = {}
            for name, value in row["fields"].items():
                formatted = compact(name, value) if name in text_names else value
                changed += formatted != value
                fields[rename.get(name, name)] = formatted
            desired[row["record_id"]] = fields
        plans[role] = {
            "schema": schema,
            "rename": rename,
            "desired": desired,
            "changed_cells": changed,
        }
        client.config["_compact_display"]["rename_ids"] += [
            f["id"] for f in schema if f["name"] in rename
        ]
    save_json(root / "display-plan.json", plans)
    current_tables = {t["id"]: t for t in client.call("+table-list")["tables"]}
    if not (root / "started.json").exists():
        if any(
            current_tables[t["id"]]["rev"] != t["rev"]
            for t in before_tables
            if t["id"] in client.config["tables"].values()
        ):
            raise ValueError("Tables changed after backup; no writes")
        save_json(root / "started.json", {"started": True})
    summary = {}
    for role, plan in plans.items():
        tid = client.config["tables"][role]
        actual_schema = {
            f["id"]: f for f in client.call("+field-list", ["--table-id", tid])["fields"]
        }
        for field in plan["schema"]:
            if field["name"] not in plan["rename"]:
                continue
            new_name = plan["rename"][field["name"]]
            actual = actual_schema[field["id"]]
            if actual["name"] == new_name:
                continue
            if actual != field or field["type"] != "text":
                raise ValueError("Field changed since backup")
            body = {k: v for k, v in actual.items() if k != "id"}
            body["name"] = new_name
            for attempt in range(3):
                time.sleep(2)
                try:
                    client.call(
                        "+field-update",
                        ["--table-id", tid, "--field-id", field["id"], "--yes"],
                        body,
                    )
                    break
                except RuntimeError:
                    if client.last_failure.get("code") != 800004135 or attempt == 2:
                        raise
                    time.sleep(5)
                    fresh = {
                        f["id"]: f
                        for f in client.call("+field-list", ["--table-id", tid])["fields"]
                    }[field["id"]]
                    if fresh == {**field, "name": new_name}:
                        break
                    if fresh != field:
                        raise ValueError("Field changed during rate-limited rename")
        current = read_records(client, tid, "来源配置ID")
        if {r["record_id"] for r in current} != plan["desired"].keys():
            raise ValueError("Record identities changed")
        original_rows = json.loads((root / role / "records.json").read_text())
        original = {
            r["record_id"]: {plan["rename"].get(n, n): v for n, v in r["fields"].items()}
            for r in original_rows
        }
        patches = {}
        for row in current:
            rid = row["record_id"]
            patch = {}
            for name, expected in plan["desired"][rid].items():
                value = row["fields"].get(name)
                if same_value(value, expected):
                    continue
                if not same_value(value, original[rid][name]):
                    raise ValueError(f"Concurrent edit: {role}/{rid}/{name}")
                patch[name] = expected
            if patch:
                patches[rid] = patch
        if patches or not (root / role / "write-plan.json").exists():
            save_json(root / role / "write-plan.json", patches)
        items = list(patches.items())
        for start in range(0, len(items), 100):
            client.call(
                "+record-batch-update",
                ["--table-id", tid],
                {"update_records": dict(items[start : start + 100])},
            )
        print(f"formatted {role}: {len(patches)} rows / {plan['changed_cells']} cells", flush=True)
        actual = read_records(client, tid, "来源配置ID")
        if {r["record_id"] for r in actual} != plan["desired"].keys():
            raise ValueError("Record identities differ after write")
        for row in actual:
            expected = plan["desired"][row["record_id"]]
            if row["fields"].keys() != expected.keys() or any(
                not same_value(v, row["fields"].get(n)) for n, v in expected.items()
            ):
                raise ValueError(f"Read-back differs: {role}/{row['record_id']}")
        schema = client.call("+field-list", ["--table-id", tid])["fields"]
        want = [{**f, "name": plan["rename"].get(f["name"], f["name"])} for f in plan["schema"]]
        # Field-list enumeration order is unstable; the visible order is checked below.
        if {f["id"]: f for f in schema} != {f["id"]: f for f in want}:
            raise ValueError("Field identities/types/order/configuration changed")
        views = client.call("+view-list", ["--table-id", tid])["views"]
        before_views = json.loads((root / role / "views.json").read_text())
        if {v["id"] for v in views} != {v["id"] for v in before_views}:
            raise ValueError("View identities changed")
        for view in before_views:
            visible = client.call(
                "+view-get-visible-fields", ["--table-id", tid, "--view-id", view["id"]]
            )["visible_fields"]
            if visible != [plan["rename"].get(n, n) for n in view["visible"]]:
                raise ValueError("Visible field order changed")
        summary[role] = {
            "records": len(actual),
            "fields": len(schema),
            "renamed_fields": len(plan["rename"]),
            "changed_cells": plan["changed_cells"],
            "readback_verified": True,
        }
    after = {t["id"]: t for t in client.call("+table-list")["tables"]}
    if set(after) != {t["id"] for t in before_tables}:
        raise ValueError("Table identities changed")
    for table in before_tables:
        if (
            table["id"] not in {client.config["tables"][r] for r in plans}
            and after[table["id"]] != table
        ):
            raise ValueError("Unrelated table changed")
    save_json(root / "verification.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--run", type=Path, required=True, help="Revision-verified external backup folder"
    )
    args = parser.parse_args()
    print(json.dumps(execute(args.config, args.run), ensure_ascii=False))
