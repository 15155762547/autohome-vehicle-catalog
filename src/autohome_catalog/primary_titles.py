"""Use existing primary text fields as full vehicle titles, preserving source columns."""

import argparse
import copy
import json
import re
import time
from pathlib import Path

from .feishu import Client, record_index
from .human_layout import read_records, set_visible
from .storage import assert_external, save_json

TITLE = "车型全称"
KEYS = {"trims": "来源配置ID", "equipment": "来源配置ID", "special": "明细ID"}
OLD_PRIMARY = {"trims": "配置名称", "equipment": "配置名称", "special": "品牌"}


def full_title(fields):
    brand, series, spec = [fields.get(k) for k in ("品牌", "车系名称", "配置名称")]
    if not all(isinstance(s, str) and s.strip() for s in (brand, series, spec)):
        raise ValueError("Missing source component for vehicle title")
    brand, series, spec = brand.strip(), series.strip(), spec.strip()

    def normalize(s):
        return re.sub(r"\s+", "", s).casefold()

    base = brand + " " + series
    if normalize(series).startswith(normalize(brand)):
        base = series
    else:
        # Source brands can contain explicit aliases, e.g. "AITO 问界".
        tokens = brand.split()
        for index, token in enumerate(tokens):
            if len(token) >= 2 and normalize(series).startswith(normalize(token)):
                base = " ".join(tokens[:index] + tokens[index + 1 :] + [series])
                break
    return base + " " + spec


def guard(config, command, args):
    if command in {"+field-list", "+record-list", "+view-list", "+view-get-visible-fields"}:
        return
    tid = args[args.index("--table-id") + 1] if "--table-id" in args else None
    state = config["_primary_titles"]
    if tid in state and command in {
        "+field-create",
        "+record-batch-update",
        "+view-set-visible-fields",
    }:
        return
    if (
        tid in state
        and command == "+field-update"
        and args[args.index("--field-id") + 1] == state[tid]
    ):
        return
    raise ValueError(f"Outside primary-title migration: {command}")


def settled_fields(client, tid, predicate):
    for attempt in range(6):
        fields = client.call("+field-list", ["--table-id", tid])["fields"]
        if predicate(fields):
            return fields
        time.sleep(1 + attempt)
    raise ValueError("Field definition has not propagated; resume this run later")


def run(config, root):
    root = assert_external(root)
    client = Client(config)
    meta = json.loads((root / "metadata-before.json").read_text())
    before = {
        role: record_index(json.loads((root / (role + "-before.json")).read_text()), key)
        for role, key in KEYS.items()
    }
    state_path = root / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    primary_ids = {}
    for role, old in OLD_PRIMARY.items():
        info = meta[role]
        if client.config["tables"][role] != info["id"]:
            raise ValueError("Table differs from backup")
        if any(v[0] != old for v in info["visible"].values()):
            raise ValueError("Unexpected primary display field")
        primary_ids[info["id"]] = next(f["id"] for f in info["fields"] if f["name"] == old)
        if any(f["name"] == TITLE for f in info["fields"]):
            raise ValueError("A title field already exists in backup")
    client.config["_primary_titles"] = primary_ids
    plan = {
        role: {key: full_title(row["fields"]) for key, row in rows.items()}
        for role, rows in before.items()
    }
    save_json(root / "title-plan.json", plan)
    for role, key in KEYS.items():
        tid, old = meta[role]["id"], OLD_PRIMARY[role]
        pid = primary_ids[tid]
        original = next(f for f in meta[role]["fields"] if f["id"] == pid)
        if original["type"] != "text":
            raise ValueError("Primary source field must be text")
        schema = client.call("+field-list", ["--table-id", tid])["fields"]
        primary = next(f for f in schema if f["id"] == pid)
        if primary["name"] == old:
            if {f["id"]: f for f in schema} != {f["id"]: f for f in meta[role]["fields"]}:
                raise ValueError("Schema changed since backup")
            definition = copy.deepcopy(primary)
            definition.pop("id")
            definition.update(
                name=TITLE,
                description="展示名称：品牌＋车系＋配置；仅去除车系开头已包含的品牌。原始拆分字段保留。此名称可重复，关联身份使用记录ID及业务ID。",
            )
            client.call(
                "+field-update", ["--table-id", tid, "--field-id", pid, "--yes"], definition
            )
        elif primary["name"] != TITLE:
            raise ValueError("Unexpected primary name during resume")
        schema = settled_fields(
            client, tid, lambda fs: any(f["id"] == pid and f["name"] == TITLE for f in fs)
        )
        clone = next((f for f in schema if f["name"] == old), None)
        if clone is None:
            definition = copy.deepcopy(original)
            definition.pop("id")
            client.call("+field-create", ["--table-id", tid], definition)
            schema = settled_fields(
                client, tid, lambda fs: any(f["name"] == old and f["id"] != pid for f in fs)
            )
            clone = next(f for f in schema if f["name"] == old)
        if clone["id"] == pid or clone["type"] != "text":
            raise ValueError("Source field preservation failed")
        state[role] = {"primary_id": pid, "preserved_source_id": clone["id"], "source_name": old}
        save_json(state_path, state)
        current = record_index(read_records(client, tid, key), key)
        if current.keys() != before[role].keys():
            raise ValueError("Record set changed since backup")
        updates = {}
        for identity, row in current.items():
            prior = before[role][identity]
            if row["record_id"] != prior["record_id"]:
                raise ValueError("Record ID changed")
            for name, value in prior["fields"].items():
                if name != old and row["fields"].get(name) != value:
                    raise ValueError(f"Unrelated field changed: {role}/{identity}/{name}")
            desired = {TITLE: plan[role][identity], old: prior["fields"][old]}
            if row["fields"].get(TITLE) not in (prior["fields"][old], desired[TITLE]):
                raise ValueError("Primary value was manually changed")
            if row["fields"].get(old) not in (None, "", prior["fields"][old]):
                raise ValueError("Preserved source value was manually changed")
            patch = {
                name: value for name, value in desired.items() if row["fields"].get(name) != value
            }
            if patch:
                updates[row["record_id"]] = patch
        items = list(updates.items())
        for start in range(0, len(items), 200):
            patch = dict(items[start : start + 200])
            if any(set(p) - {TITLE, old} for p in patch.values()):
                raise ValueError("Unexpected title update field")
            client.call("+record-batch-update", ["--table-id", tid], {"update_records": patch})
            time.sleep(0.5)
            print(f"Titles {role}: {min(start + 200, len(items))}/{len(items)}", flush=True)
        for vid, visible in meta[role]["visible"].items():
            front = [TITLE, "model_id", "品牌", "车系名称", "配置名称"]
            if role == "special":
                front = [
                    TITLE,
                    "项目名称",
                    "类别",
                    "配置状态",
                    "内容",
                    "model_id",
                    "品牌",
                    "车系名称",
                    "配置名称",
                ]
            names = [TITLE if n == old else n for n in visible]
            set_visible(
                client,
                ["--table-id", tid, "--view-id", vid],
                list(dict.fromkeys(front + names + [old])),
            )
        after = record_index(read_records(client, tid, key), key)
        if after.keys() != before[role].keys():
            raise ValueError("Post-write record set differs")
        for identity, prior in before[role].items():
            expected = {**prior, "fields": {**prior["fields"], TITLE: plan[role][identity]}}
            if after[identity] != expected:
                raise ValueError(f"Source values or relationships changed: {role}/{identity}")
        actual = client.call("+field-list", ["--table-id", tid])["fields"]
        old_ids = {f["id"] for f in meta[role]["fields"]}
        if len(actual) != len(old_ids) + 1 or not old_ids.issubset({f["id"] for f in actual}):
            raise ValueError("Unexpected schema additions or removals")
        for field in meta[role]["fields"]:
            if field["id"] != pid and field != next(f for f in actual if f["id"] == field["id"]):
                raise ValueError("Unrelated schema changed")
        save_json(root / (role + "-after.json"), list(after.values()))
        print(f"Verified all source values and relationships: {role}/{len(after)}", flush=True)
    result = {
        "rows": {role: len(rows) for role, rows in before.items()},
        "original_fields_and_values_preserved": True,
        "record_ids_and_links_unchanged": True,
        "added_columns_per_table": 1,
        "extra_freeze_configured": False,
    }
    save_json(root / "verification.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.config, args.root), ensure_ascii=False))
