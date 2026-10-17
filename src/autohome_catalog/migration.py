"""One-time, backed-up legacy rename and fresh experimental schema creation."""

import hashlib
import json
import time
from pathlib import Path

from .export import load_dictionary
from .feishu import TABLE_NAMES, table_schemas
from .storage import assert_external, save_json


def bootstrap(client, backup: Path):
    assert_external(backup)
    verified = json.loads((backup / "verification.json").read_text())
    if verified.get("revisions_unchanged") is not True:
        raise ValueError("A complete, revision-verified backup is required")
    for table in verified["tables"]:
        for page in table["pages"]:
            if hashlib.sha256((backup / page["file"]).read_bytes()).hexdigest() != page["sha256"]:
                raise ValueError("Backup hash mismatch")
    saved = {t["id"]: t for t in verified["tables"]}
    before = client.call("+table-list")["tables"]
    current = {t["id"]: t for t in before}
    for role, table_id in client.config["legacy_tables"].items():
        original, live = saved[table_id], current[table_id]
        desired_name = TABLE_NAMES[role] + "（已弃用）"
        if live["records_count"] != original["records_count"] or live["rev"] != original["rev"]:
            raise ValueError("Legacy records/schema changed since backup; make a fresh backup")
        if live["name"] not in {TABLE_NAMES[role], desired_name}:
            raise ValueError("Unexpected legacy table name")
        if live["name"] != desired_name:
            args = ["--block-id", table_id, "--name", desired_name]
            client.call("+base-block-rename", args, dry_run=True)
            client.call("+base-block-rename", args)
    dictionary = load_dictionary()
    for role, name in TABLE_NAMES.items():
        tables = client.call("+table-list")["tables"]
        existing = [t for t in tables if t["name"] == name]
        if existing:
            if client.config.get("tables", {}).get(role) != existing[0]["id"]:
                raise ValueError(f"Unrecognized table already uses the new name: {name}")
            continue
        ids = {"models": "pending", "trims": "pending", **client.config.get("tables", {})}
        fields = table_schemas(dictionary, ids)[role]
        save_json(client.root / f"{role}-schema-request.json", fields)
        client.call(
            "+table-create", ["--name", name, "--fields", json.dumps(fields, ensure_ascii=False)]
        )
        for attempt in range(4):
            tables = client.call("+table-list")["tables"]
            found = [t for t in tables if t["name"] == name]
            if len(found) == 1:
                break
            if attempt == 3:
                raise ValueError("New table ID not visible yet; inspect receipt before retrying")
            time.sleep(0.5)
        client.config.setdefault("tables", {})[role] = found[0]["id"]
        save_json(client.config_path, client.config)
    client.validate_schemas(dictionary)
    after = client.call("+table-list")["tables"]
    for role, table_id in client.config["legacy_tables"].items():
        live = next(t for t in after if t["id"] == table_id)
        if (
            live["name"] != TABLE_NAMES[role] + "（已弃用）"
            or live["records_count"] != saved[table_id]["records_count"]
        ):
            raise ValueError("Legacy identity/name/count verification failed")
    result = {
        "before": before,
        "after": after,
        "new_tables": client.config["tables"],
        "backup_hashes_verified": True,
        "deleted_tables": [],
        "deleted_records": [],
    }
    save_json(client.root / "migration-verification.json", result)
    return result["new_tables"]
