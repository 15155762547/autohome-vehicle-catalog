"""Choose numeric display precision from live values without writing any records."""

import argparse
import copy
import json
import time
from decimal import Decimal
from pathlib import Path

from .feishu import Client, record_index
from .storage import assert_external, save_json

KEYS = {"models": "model_id", "trims": "来源配置ID", "equipment": "来源配置ID", "special": "明细ID"}


def guard(config, command, args):
    if command in {"+field-list", "+record-list"}:
        return
    if command == "+field-update":
        tid = args[args.index("--table-id") + 1]
        fid = args[args.index("--field-id") + 1]
        if [tid, fid] in config["_number_format"]:
            return
    raise ValueError(f"Outside numeric formatting: {command}")


def precision(values):
    digits = []
    for value in values:
        if value is None or value == "":
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Unexpected numeric value: {value!r}")
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("Nonfinite numeric value")
        digits.append(max(0, -number.normalize().as_tuple().exponent))
    return max(digits) if digits else None


def read_numbers(client, role, fields):
    tid = client.config["tables"][role]
    key_field = (
        client.config.get("color_row_key", "颜色明细ID")
        if role in client.config.get("color_tables", {})
        else KEYS.get(role, "明细ID" if role in {"特色配置", "选装包"} else "来源配置ID")
    )
    combined, revision = {}, None
    for start in range(0, len(fields), 49):
        rows, offset = [], 0
        while True:
            args = ["--table-id", tid, "--limit", "200", "--offset", str(offset)]
            for field in [key_field, *[f["id"] for f in fields[start : start + 49]]]:
                args += ["--field-id", field]
            data = client.call("+record-list", args)
            if revision is not None and revision != data["rev"]:
                raise ValueError("Table changed during numeric export; rerun before applying")
            revision = data["rev"]
            rows.extend(
                {"record_id": rid, "fields": dict(zip(data["fields"], values, strict=True))}
                for rid, values in zip(data["record_id_list"], data["data"], strict=True)
            )
            if not data["has_more"]:
                break
            if not data["data"]:
                raise ValueError("Empty page with has_more")
            offset += len(data["data"])
        indexed = record_index(rows, key_field)
        if combined and indexed.keys() != combined.keys():
            raise ValueError("Record identity changed between numeric column batches")
        for key, row in indexed.items():
            if key in combined:
                if combined[key]["record_id"] != row["record_id"]:
                    raise ValueError("Record ID changed between numeric column batches")
                combined[key]["fields"].update(row["fields"])
            else:
                combined[key] = row
    return combined


def run(config, root, apply=False):
    root = assert_external(root)
    root.mkdir(parents=True, exist_ok=True)
    client = Client(config)
    if client.config.get("category_tables"):
        client.config["tables"] = {
            "models": client.config["tables"]["models"],
            "trims": client.config["tables"]["trims"],
            **client.config["category_tables"],
        }
    if (root / "audit.json").exists():
        schemas, before, plan, audit = [
            json.loads((root / name).read_text())
            for name in ("schemas-before.json", "numbers-before.json", "plan.json", "audit.json")
        ]
    else:
        schemas, before, plan, audit = {}, {}, [], []
        for role in client.config["tables"]:
            tid = client.config["tables"][role]
            schemas[role] = client.call("+field-list", ["--table-id", tid])["fields"]
            fields = [f for f in schemas[role] if f["type"] == "number"]
            if not fields:
                continue
            before[role] = read_numbers(client, role, fields)
            for field in fields:
                needed = precision(
                    row["fields"].get(field["name"]) for row in before[role].values()
                )
                current = field.get("style", {}).get("precision", 2)
                plain = field.get("style", {}).get("type", "plain") == "plain"
                target = min(needed, 4) if needed is not None and plain else current
                item = {
                    "role": role,
                    "field": field["name"],
                    "old": current,
                    "new": target,
                    "required": needed,
                    "changed": current != target,
                }
                audit.append(item)
                if current != target:
                    definition = copy.deepcopy(field)
                    fid = definition.pop("id")
                    definition.setdefault("style", {})["precision"] = target
                    plan.append({"table_id": tid, "field_id": fid, "definition": definition})
            print(
                f"Checked {role}: {len(before[role])} rows, {len(fields)} number columns",
                flush=True,
            )
        save_json(root / "schemas-before.json", schemas)
        save_json(root / "numbers-before.json", before)
        save_json(root / "plan.json", plan)
        save_json(root / "audit.json", audit)
    if not apply:
        return {"planned": len(plan), "columns": len(audit)}
    client.config["_number_format"] = [[p["table_id"], p["field_id"]] for p in plan]
    current = {
        tid: {f["id"]: f for f in client.call("+field-list", ["--table-id", tid])["fields"]}
        for tid in client.config["tables"].values()
    }
    for index, item in enumerate(plan, 1):
        tid, fid = item["table_id"], item["field_id"]
        desired = {"id": fid, **item["definition"]}
        if current[tid][fid] == desired:
            continue
        original = next(f for fs in schemas.values() for f in fs if f["id"] == fid)
        if current[tid][fid] != original:
            raise ValueError(f"Field changed since backup: {fid}")
        for attempt in range(3):
            time.sleep(1.5 + attempt * 2)
            try:
                client.call(
                    "+field-update",
                    ["--table-id", tid, "--field-id", fid, "--yes"],
                    item["definition"],
                )
                break
            except RuntimeError:
                if client.last_failure.get("code") != 800004135 or attempt == 2:
                    raise
                fresh = client.call("+field-list", ["--table-id", tid])["fields"]
                actual = next(f for f in fresh if f["id"] == fid)
                if actual == desired:
                    break
                if actual != original:
                    raise ValueError("Field changed during rate-limit recovery")
        print(f"Formatted {index}/{len(plan)}: {item['definition']['name']}", flush=True)
    after_schemas = {}
    for role, expected in schemas.items():
        tid = client.config["tables"][role]
        actual = client.call("+field-list", ["--table-id", tid])["fields"]
        intended = copy.deepcopy(expected)
        for field in intended:
            patch = next(
                (p for p in plan if p["table_id"] == tid and p["field_id"] == field["id"]), None
            )
            if patch:
                field.update(patch["definition"])
        if {f["id"]: f for f in actual} != {f["id"]: f for f in intended}:
            raise ValueError(f"Schema read-back mismatch: {role}")
        after_schemas[role] = actual
        numeric = [f for f in actual if f["type"] == "number"]
        if numeric and read_numbers(client, role, numeric) != before[role]:
            raise ValueError(f"Numeric values changed: {role}")
        print(f"Verified schema and unchanged numbers: {role}", flush=True)
    save_json(root / "schemas-after.json", after_schemas)
    result = {
        "checked_columns": len(audit),
        "changed_columns": len(plan),
        "integer_columns": sum(x["required"] == 0 for x in audit),
        "decimal_columns": sum(x["required"] is not None and x["required"] > 0 for x in audit),
        "numeric_values_unchanged": True,
    }
    save_json(root / "verification.json", result)
    lines = [
        "# 数字显示调整",
        "",
        "只改数字列的显示精度，完整回读确认数值和记录ID未变化。",
        "",
        "飞书按整列设置小数位：全为整数的列设为0位；含真实小数的列保留所需位数。同列整数仍可能显示补零。全空列保持原设置。",
        "",
        f"检查{len(audit)}列，调整{len(plan)}列。",
        "",
        "| 表 | 字段 | 原小数位 | 现小数位 |",
        "|---|---|---:|---:|",
    ]
    lines += [f"| {x['role']} | {x['field']} | {x['old']} | {x['new']} |" for x in audit]
    (root / "数字显示调整.md").write_text("\n".join(lines) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.config, args.root, args.apply), ensure_ascii=False))
