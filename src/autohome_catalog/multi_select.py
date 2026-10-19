"""Convert explicitly enumerated compound functions to multiselect without losing evidence."""

import argparse
import json
import re
import time
from pathlib import Path

from .base_utils import read_records, set_visible, settled_fields
from .feishu import OMITTED_DIMENSIONS, Client, record_index
from .parse import FAMILIES
from .storage import assert_external, save_json

BACKUP = "多选转换原文JSON"
COLORS = ["Blue", "Turquoise", "Purple", "Green", "Wathet", "Carmine", "Lime"]


def tokens(value):
    if value in (None, ""):
        return []
    if not isinstance(value, str):
        raise ValueError("Compound source must be text")
    # Split only separators following a complete equipment state, never label slashes.
    text = re.sub(r"((?:标配|选配|无|-|—)(?:（[^\n]*?）)?)/", r"\1\n", value)
    result = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line in {"-", "—", "无", "不支持", "不配备"}:
            result.append("无")
        elif re.fullmatch(r"(?:标配|选配)(?:（.*）)?", line):
            match = re.fullmatch(r"(标配|选配)(?:（(.*)）)?", line)
            state, price = match.groups()
            details = (["选配"] if state == "选配" else []) + ["未列明功能"]
            if price:
                details.append(price)
            result.append("支持（" + "，".join(details) + "）")
        elif line.startswith("价格条件："):
            result.append(line)
        else:
            match = re.fullmatch(r"(.+?)\s+(标配|选配|无|-|—)(（.*）)?", line)
            if not match:
                match = re.fullmatch(r"(主|副|前|后)(-|—)()", line)
            if not match:
                raise ValueError(f"Unrecognized compound item: {line}")
            label, state, price = match.groups()
            suffix = price or ""
            if state == "选配":
                suffix = "（选配" + ("，" + price[1:-1] if price else "") + "）"
            elif state in {"无", "-", "—"}:
                suffix = "（无）" + suffix
            result.append(label + suffix)
    return list(dict.fromkeys(result))


def candidates(fields):
    result = []
    for field in fields:
        match = re.search(r"原名=(.*?)。", field.get("description", ""))
        if field["type"] == "text" and match and match[1] in FAMILIES:
            result.append(field)
    return result


def guard(config, command, args):
    if command in {
        "+field-list",
        "+field-search-options",
        "+record-list",
        "+view-list",
        "+view-get-visible-fields",
    }:
        return
    tid = args[args.index("--table-id") + 1] if "--table-id" in args else None
    allowed = config["_multi_select"]
    if tid in allowed and command in {
        "+field-create",
        "+record-batch-update",
        "+view-set-visible-fields",
    }:
        return
    if (
        tid in allowed
        and command == "+field-update"
        and args[args.index("--field-id") + 1] in allowed[tid]
    ):
        return
    raise ValueError(f"Outside compound multiselect conversion: {command}")


def indexed(client, tid):
    return record_index(read_records(client, tid, "来源配置ID"), "来源配置ID")


def all_options(client, tid, field):
    if not field.get("truncated"):
        return field["options"]
    result = []
    while True:
        data = client.call(
            "+field-search-options",
            [
                "--table-id",
                tid,
                "--field-id",
                field["id"],
                "--limit",
                "200",
                "--offset",
                str(len(result)),
            ],
        )
        result.extend(data["options"])
        if len(result) == data["total"]:
            return result
        if not data["options"] or len(result) > data["total"]:
            raise ValueError("Option pagination is inconsistent")


def write(client, tid, updates):
    items = list(updates.items())
    for start in range(0, len(items), 200):
        client.call(
            "+record-batch-update",
            ["--table-id", tid],
            {"update_records": dict(items[start : start + 200])},
        )
        time.sleep(0.5)
        print(f"Written {tid}: {min(start + 200, len(items))}/{len(items)}", flush=True)


def original_fields_equal(original, actual, excluded):
    for name, value in original.items():
        if name in excluded or (name in OMITTED_DIMENSIONS and name not in actual):
            continue
        if actual.get(name) != value:
            raise ValueError(f"Unrelated source field changed: {name}")


def prepare(root):
    meta = json.loads((root / "metadata-before.json").read_text())
    plan, skipped = {}, []
    for role, info in meta.items():
        rows = record_index(json.loads((root / (role + "-before.json")).read_text()), "来源配置ID")
        plan[role] = []
        for field in candidates(info["fields"]):
            try:
                values = {
                    key: tokens(row["fields"].get(field["name"])) for key, row in rows.items()
                }
            except ValueError as error:
                skipped.append({"role": role, "field": field["name"], "reason": str(error)})
                continue
            names = list(dict.fromkeys(n for v in values.values() for n in v))
            if not names or len(names) > 10000:
                skipped.append(
                    {
                        "role": role,
                        "field": field["name"],
                        "reason": "No options or too many options",
                    }
                )
                continue
            options = [
                {
                    "name": n,
                    "hue": "Orange"
                    if "（选配" in n
                    else "Gray"
                    if n == "无" or n.endswith("（无）") or n.startswith("价格条件：")
                    else COLORS[i % len(COLORS)],
                    "lightness": "Lighter",
                }
                for i, n in enumerate(names)
            ]
            definition = {
                "name": field["name"],
                "type": "select",
                "multiple": True,
                "options": options,
                "default_value": None,
                "description": field.get("description", "")
                + " 多选：无后缀为明确标配；选配、价格、无及整项价格条件保留文字标记。空白为未披露。完整原值在隐藏字段“"
                + BACKUP
                + "”。",
            }
            plan[role].append({"id": field["id"], "definition": definition, "values": values})
    save_json(root / "plan.json", plan)
    save_json(root / "skipped.json", skipped)
    return plan


def apply(config, root):
    root = assert_external(root)
    plan = json.loads((root / "plan.json").read_text())
    meta = json.loads((root / "metadata-before.json").read_text())
    client = Client(config)
    client.config["_multi_select"] = {
        info["id"]: [f["id"] for f in plan[role]] for role, info in meta.items()
    }
    results = {}
    for role, info in meta.items():
        tid = info["id"]
        if tid != client.config["tables"][role]:
            raise ValueError("Table identity differs from backup")
        before = record_index(
            json.loads((root / (role + "-before.json")).read_text()), "来源配置ID"
        )
        fields = {p["definition"]["name"]: p for p in plan[role]}
        if not fields:
            continue
        schema = client.call("+field-list", ["--table-id", tid])["fields"]
        if not any(f["name"] == BACKUP for f in schema):
            client.call(
                "+field-create",
                ["--table-id", tid],
                {
                    "name": BACKUP,
                    "type": "text",
                    "description": "多选转换前各复合功能字段的完整原文，按来源配置ID关联；保留状态、重复项、价格及条件。",
                },
            )
            settled_fields(client, tid, lambda fs: any(f["name"] == BACKUP for f in fs))
        current = indexed(client, tid)
        if current.keys() != before.keys():
            raise ValueError("Configuration set changed before conversion")
        backup_values, updates = {}, {}
        for key, old in before.items():
            if current[key]["record_id"] != old["record_id"]:
                raise ValueError("Record identity changed")
            original_fields_equal(old["fields"], current[key]["fields"], set(fields))
            for name, p in fields.items():
                value = current[key]["fields"].get(name)
                definition = next(f for f in schema if f["id"] == p["id"])
                if definition["type"] == "text":
                    unchanged = value == old["fields"].get(name)
                else:
                    unchanged = value in (None, []) or (
                        isinstance(value, list) and sorted(value) == sorted(p["values"][key])
                    )
                if not unchanged:
                    raise ValueError(f"Compound value changed since backup: {key}/{name}")
            raw = json.dumps({n: old["fields"].get(n) for n in fields}, ensure_ascii=False)
            backup_values[key] = raw
            existing = current[key]["fields"].get(BACKUP)
            if existing not in (None, "", raw):
                raise ValueError("Backup belongs to a different conversion")
            if existing != raw:
                updates[old["record_id"]] = {BACKUP: raw}
        write(client, tid, updates)
        checked = indexed(client, tid)
        if checked.keys() != before.keys() or any(
            checked[k]["fields"].get(BACKUP) != v for k, v in backup_values.items()
        ):
            raise ValueError("Raw backup read-back failed; no type conversion allowed")
        print(f"Raw backups verified: {role}/{len(checked)}", flush=True)
        actual = client.call("+field-list", ["--table-id", tid])["fields"]
        for p in plan[role]:
            existing = next(f for f in actual if f["id"] == p["id"])
            if existing["type"] == "select" and existing.get("multiple") is True:
                if {o["name"] for o in all_options(client, tid, existing)} != {
                    o["name"] for o in p["definition"]["options"]
                }:
                    raise ValueError("Existing multiselect options differ")
                continue
            original = next(f for f in info["fields"] if f["id"] == p["id"])
            if existing != original:
                raise ValueError("Field changed since backup")
            time.sleep(1.5)
            client.call(
                "+field-update",
                ["--table-id", tid, "--field-id", p["id"], "--yes"],
                p["definition"],
            )
            print("Multiselect field:", role, p["definition"]["name"], flush=True)
        settled_fields(
            client,
            tid,
            lambda fs: all(
                any(
                    f["id"] == p["id"] and f["type"] == "select" and f.get("multiple") is True
                    for f in fs
                )
                for p in plan[role]
            ),
        )
        updates = {
            row["record_id"]: {name: p["values"][key] for name, p in fields.items()}
            for key, row in before.items()
        }
        write(client, tid, updates)
        after = indexed(client, tid)
        if after.keys() != before.keys():
            raise ValueError("Configuration set changed after conversion")
        for key, old in before.items():
            row = after[key]
            if (
                row["record_id"] != old["record_id"]
                or row["fields"].get(BACKUP) != backup_values[key]
            ):
                raise ValueError("Identity or backup changed after conversion")
            original_fields_equal(old["fields"], row["fields"], set(fields))
            for name, p in fields.items():
                if sorted(row["fields"].get(name) or []) != sorted(p["values"][key]):
                    raise ValueError(f"Multiselect read-back differs: {key}/{name}")
        schema = client.call("+field-list", ["--table-id", tid])["fields"]
        byid = {f["id"]: f for f in schema}
        changed = {p["id"] for p in plan[role]}
        for original in info["fields"]:
            if original["name"] in OMITTED_DIMENSIONS and original["id"] not in byid:
                continue
            if original["id"] not in changed and byid.get(original["id"]) != original:
                raise ValueError("Unrelated schema changed")
        names = {f["name"] for f in schema}
        for vid, visible in info["visible"].items():
            set_visible(
                client,
                ["--table-id", tid, "--view-id", vid],
                [n for n in visible if n in names and n != BACKUP],
            )
        save_json(root / (role + "-after.json"), list(after.values()))
        results[role] = {"rows": len(after), "fields": list(fields)}
        print(f"Verified multiselect and untouched source values: {role}", flush=True)
    save_json(root / "verification.json", results)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        print(json.dumps(apply(args.config, args.root), ensure_ascii=False))
    else:
        result = prepare(assert_external(args.root))
        print({role: len(fields) for role, fields in result.items()})
