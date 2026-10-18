"""Plan/apply/read-back for explicitly configured experimental tables only."""

import hashlib
import json
import math
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from .export import EQUIPMENT_FIELDS, MODEL_FIELDS, TRIM_FIELDS, load_dictionary, raw_column
from .storage import assert_external, save_json

KEY_FIELDS = {"models": "来源车系ID", "trims": "来源配置ID", "equipment": "来源配置ID"}
TABLE_NAMES = {"models": "vehicle_models", "trims": "vehicle_trims", "equipment": "配置参数扩展"}


def assert_target(config):
    token = config.get("base_token")
    if (
        config.get("purpose") != "experiment"
        or not token
        or token != config.get("allowed_base_token")
        or token in config.get("production_base_tokens", [])
        or not config.get("profile")
    ):
        raise ValueError(
            "Unapproved write target; use the explicit experimental Base configuration"
        )
    if set(config.get("tables", {}).values()) & set(config.get("legacy_tables", {}).values()):
        raise ValueError("New-table operations cannot target legacy tables")


def assert_operation(config, command, args):
    if config.get("_number_format"):
        from .number_format import guard

        return guard(config, command, args)
    if config.get("_image_merge"):
        from .image_merge import guard

        return guard(config, command, args)
    if config.get("_boolean_migration"):
        from .boolean_migration import guard

        return guard(config, command, args)
    if config.get("_compact_display"):
        from .compact_display import guard

        return guard(config, command, args)
    if config.get("_model_grain_operation"):
        from .model_grain import guard

        return guard(config, command, args)
    if config.get("_image_operation"):
        from .image_sync import assert_image_operation

        return assert_image_operation(config, command, args)
    if config.get("human_layout_migration"):
        from .human_layout import assert_migration_operation

        return assert_migration_operation(config, command, args)
    if config.get("layout_version") == 2 and command in {
        "+table-create",
        "+base-block-rename",
        "+record-batch-create",
        "+record-batch-update",
        "+field-create",
    }:
        raise ValueError(
            "Legacy writes are disabled for the human layout; use a reviewed migration"
        )
    allowed = {
        "+table-list",
        "+field-list",
        "+record-list",
        "+table-create",
        "+base-block-rename",
        "+record-batch-create",
        "+record-batch-update",
        "+field-create",
        "+view-list",
        "+view-get-visible-fields",
        "+view-set-visible-fields",
    }
    if command not in allowed:
        raise ValueError(f"Unsupported command: {command}")
    if command in {
        "+record-batch-create",
        "+record-batch-update",
        "+field-create",
        "+view-set-visible-fields",
    }:
        if (
            "--table-id" not in args
            or args[args.index("--table-id") + 1] not in config["tables"].values()
        ):
            raise ValueError("Write table is outside the configured new tables")
    if command == "+base-block-rename":
        block = args[args.index("--block-id") + 1]
        role = next((r for r, i in config["legacy_tables"].items() if i == block), None)
        if role is None or args[args.index("--name") + 1] != TABLE_NAMES[role] + "（已弃用）":
            raise ValueError("Only the two explicitly configured legacy renames are allowed")
    if command == "+table-create":
        name = args[args.index("--name") + 1]
        additional = config.get("extension_table_names", [])
        if name not in TABLE_NAMES.values() and not (
            name in additional and re.fullmatch(r"配置参数扩展[2-9]\d*", name)
        ):
            raise ValueError("Unexpected new table name")


def same_value(left, right):
    if left in (None, "") and right in (None, ""):
        return True
    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left == right
    if isinstance(left, str) and isinstance(right, str):
        left_link = re.fullmatch(r"\[[^\]]*\]\((https?://[^\s]+)\)", left)
        right_link = re.fullmatch(r"\[[^\]]*\]\((https?://[^\s]+)\)", right)
        left = left_link.group(1) if left_link else left
        right = right_link.group(1) if right_link else right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-8)
    if isinstance(left, str) and isinstance(right, str) and "T" in left and "T" in right:
        try:
            return datetime.fromisoformat(left.replace("Z", "+00:00")) == datetime.fromisoformat(
                right.replace("Z", "+00:00")
            )
        except ValueError:
            pass
    if isinstance(left, list) and isinstance(right, list):
        return sorted(json.dumps(x, sort_keys=True) for x in left) == sorted(
            json.dumps(x, sort_keys=True) for x in right
        )
    return left == right


def record_index(records, key_field):
    result = {}
    for row in records:
        key = row["fields"].get(key_field)
        if not key or str(key) in result:
            raise ValueError(f"Duplicate or missing stable key: {key_field}/{key}")
        result[str(key)] = row
    return result


def diff_records(desired, current, key_field):
    old = record_index(current, key_field)
    if len({row["key"] for row in desired}) != len(desired):
        raise ValueError("Duplicate desired business keys")
    changes = {"create": [], "update": {}}
    for row in desired:
        previous = old.get(row["key"])
        if previous is None:
            changes["create"].append(row["fields"])
        else:
            patch = {
                k: v
                for k, v in row["fields"].items()
                if not same_value(previous["fields"].get(k), v)
            }
            if patch:
                changes["update"][previous["record_id"]] = patch
    return changes


def table_schemas(dictionary, tables):
    def meta(name):
        if name == "来源采集时间":
            return {"name": name, "type": "datetime", "style": {"format": "yyyy-MM-dd HH:mm"}}
        if name == "销售状态":
            return {
                "name": name,
                "type": "select",
                "multiple": False,
                "options": [{"name": "在售"}, {"name": "预售"}],
            }
        field = {"name": name, "type": "text"}
        if name in {"来源车系页", "来源参数页", "来源采集URL", "车系图片URL", "图片来源页"}:
            field["style"] = {"type": "url"}
        return field

    schemas = {
        "models": [meta(n) for n in MODEL_FIELDS],
        "trims": [meta(n) for n in TRIM_FIELDS],
        "equipment": [meta(n) for n in EQUIPMENT_FIELDS],
    }
    schemas["trims"].append(
        {
            "name": "所属车系",
            "type": "link",
            "link_table": tables["models"],
            "bidirectional": True,
            "bidirectional_link_field_name": "配置列表",
        }
    )
    schemas["equipment"].append(
        {
            "name": "对应配置",
            "type": "link",
            "link_table": tables["trims"],
            "bidirectional": True,
            "bidirectional_link_field_name": "参数扩展",
        }
    )
    for field in dictionary:
        definition = {
            "name": field["column"],
            "type": field["kind"],
            "description": f"汽车之家{field['group']}/{field['section']}；原始参数ID={field['item_id']}；原名={field['name']}。●标配/○选配；横杠依原字段语义核对，条件与原符号保留。",
        }
        if field["kind"] == "number":
            definition["style"] = {"type": "plain", "precision": 4}
        schemas[field["table"]].append(definition)
        if field["kind"] == "number":
            schemas[field["table"]].append(
                {
                    "name": raw_column(field),
                    "type": "text",
                    "description": "该数值参数的源站原文/符号/条件，不把短横杠变为0或false。",
                }
            )
    # These reverse links are created by the opposite table, so reserve their slots.
    if (
        len(schemas["models"]) + 1 > 300
        or len(schemas["trims"]) + 1 > 300
        or len(schemas["equipment"]) > 300
    ):
        raise ValueError("Schema exceeds 300 columns")
    return schemas


def schema_delta(expected, actual):
    by_name = {field["name"]: field for field in actual}
    if len(by_name) != len(actual):
        raise ValueError("Schema contains duplicate field names")
    missing = []
    for field in expected:
        old = by_name.get(field["name"])
        if old is None:
            missing.append(field)
        elif old["type"] != field["type"] or (
            field["type"] == "link" and old.get("link_table") != field["link_table"]
        ):
            raise ValueError(
                f"Schema mismatch: {field['name']}; existing fields are never replaced"
            )
    return missing


class Client:
    def __init__(self, config_path: Path):
        self.config_path = assert_external(config_path)
        self.config = json.loads(config_path.read_text())
        assert_target(self.config)
        self.root = config_path.parent / "feishu-audit"
        self.root.mkdir(exist_ok=True)

    def call(self, command, args=(), body=None, dry_run=False):
        assert_target(self.config)
        assert_operation(self.config, command, args)
        prefix = self.root / f"{time.time_ns()}-{command.lstrip('+')}"
        argv = [
            "lark-cli",
            "base",
            command,
            "--base-token",
            self.config["base_token"],
            "--profile",
            self.config["profile"],
            "--as",
            "user",
            "--format",
            "json",
            *args,
        ]
        if body is not None:
            payload = prefix.with_suffix(".request.json")
            save_json(payload, body)
            argv += ["--json", "@" + str(payload.relative_to(self.config_path.parent))]
        if dry_run:
            argv.append("--dry-run")
        for attempt in range(3):
            result = subprocess.run(
                argv, cwd=self.config_path.parent, capture_output=True, text=True
            )
            output = result.stdout if result.returncode == 0 else result.stderr
            prefix.with_suffix(".response.json").write_text(output)
            if not result.returncode:
                break
            try:
                failure = json.loads(output).get("error", {})
            except ValueError:
                failure = {}
            transient = (
                failure.get("code") in (800100001, 800004135)
                or (failure.get("code") == 2200 and failure.get("retryable") is True)
                or (failure.get("type") == "network" and failure.get("subtype") == "transport")
            )
            if (
                command
                not in {
                    "+record-list",
                    "+field-list",
                    "+table-list",
                    "+view-list",
                    "+view-get-visible-fields",
                    "+view-get-sort",
                    "+view-get-filter",
                    "+view-get-group",
                    "+base-block-list",
                }
                or not transient
                or attempt == 2
            ):
                break
            prefix.with_name(prefix.name + f"-attempt{attempt + 1}.response.json").write_text(
                output
            )
            time.sleep(1 + attempt)
        if result.returncode:
            try:
                self.last_failure = json.loads(output).get("error", {})
            except ValueError:
                self.last_failure = {}
            raise RuntimeError(f"{command} failed; inspect {prefix.with_suffix('.response.json')}")
        envelope = json.loads(output)
        if envelope.get("ok") is not True or envelope.get("identity") != "user":
            raise ValueError(f"Unconfirmed user response: {command}")
        data = envelope.get("data", {})
        if data.get("ignored_fields"):
            raise ValueError(f"Ignored fields during {command}")
        return data

    def records(self, role, field_names=None):
        for attempt in range(4):
            try:
                return self._records_once(role, field_names)
            except ValueError as error:
                if str(error) != "Table changed during record export" or attempt == 3:
                    raise
                time.sleep(min(2**attempt, 4))

    def _records_once(self, role, field_names=None):
        table = self.config["tables"][role]
        rows, offset = [], 0
        revision = None
        while True:
            args = ["--table-id", table, "--offset", str(offset), "--limit", "200"]
            for name in field_names or []:
                args += ["--field-id", name]
            data = self.call("+record-list", args)
            if revision is not None and revision != data["rev"]:
                raise ValueError("Table changed during record export")
            revision = data["rev"]
            if len(data["record_id_list"]) != len(data["data"]):
                raise ValueError("Record matrix identity mismatch")
            rows.extend(
                {"record_id": rid, "fields": dict(zip(data["fields"], values, strict=True))}
                for rid, values in zip(data["record_id_list"], data["data"], strict=True)
            )
            if not data["has_more"]:
                record_index(rows, self.config.get("key_fields", {}).get(role, KEY_FIELDS[role]))
                return rows
            if not data["data"]:
                raise ValueError("Empty record page with has_more=true")
            offset += len(data["data"])

    def validate_schemas(self, dictionary):
        definitions = table_schemas(dictionary, self.config["tables"])
        for role, expected in definitions.items():
            for attempt in range(5):
                actual = self.call("+field-list", ["--table-id", self.config["tables"][role]])[
                    "fields"
                ]
                if not schema_delta(expected, actual):
                    break
                if attempt == 4:
                    raise ValueError(
                        f"Schema has missing columns: {role}; inspect successful write receipts before retrying"
                    )
                time.sleep(min(0.25 * 2**attempt, 2))


def sync_schema(client, dictionary_path=None):
    dictionary = load_dictionary(dictionary_path)
    definitions = table_schemas(dictionary, client.config["tables"])
    additions = {}
    for role, expected in definitions.items():
        actual = client.call("+field-list", ["--table-id", client.config["tables"][role]])["fields"]
        additions[role] = schema_delta(expected, actual)
        if len(actual) + len(additions[role]) > 300:
            raise ValueError(f"Schema extension would exceed 300 columns: {role}")
    save_json(client.root / "schema-extension-plan.json", additions)
    for role, fields in additions.items():
        if fields:
            args = ["--table-id", client.config["tables"][role]]
            client.call("+field-create", args, fields, dry_run=True)
            client.call("+field-create", args, fields)
    client.validate_schemas(dictionary)
    result = {role: len(fields) for role, fields in additions.items()}
    save_json(client.root / "schema-extension-verification.json", result)
    return result


def resolved_rows(role, desired, snapshots):
    rows = []
    parents = record_index(snapshots["models"], KEY_FIELDS["models"])
    trims = record_index(snapshots["trims"], KEY_FIELDS["trims"])
    for row in desired[role]:
        fields = dict(row["fields"])
        if role == "trims":
            parent = parents.get(row["model_key"])
            fields["所属车系"] = (
                [{"id": parent["record_id"]}]
                if parent
                else [{"pending_model_key": row["model_key"]}]
            )
        if role == "equipment":
            parent = trims.get(row["trim_key"])
            fields["对应配置"] = (
                [{"id": parent["record_id"]}] if parent else [{"pending_trim_key": row["trim_key"]}]
            )
        rows.append({"key": row["key"], "fields": fields})
    return rows


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def snapshots_for(client, desired):
    snapshots = {}
    snapshots["models"] = client.records("models") if desired["models"] or desired["trims"] else []
    if desired["trims"]:
        snapshots["trims"] = client.records("trims")
    elif desired["equipment"]:
        snapshots["trims"] = client.records(
            "trims", ["来源配置ID", client.config.get("equipment_reverse_field", "参数扩展")]
        )
    else:
        snapshots["trims"] = []
    snapshots["equipment"] = client.records("equipment") if desired["equipment"] else []
    return snapshots


def plan(client, run: Path, dictionary_path: Path | None = None):
    assert_external(run)
    dictionary = load_dictionary(dictionary_path)
    client.validate_schemas(dictionary)
    desired = json.loads((run / "desired.json").read_text())
    snapshots = snapshots_for(client, desired)
    changes = {
        role: diff_records(
            resolved_rows(role, desired, snapshots), snapshots[role], KEY_FIELDS[role]
        )
        for role in TABLE_NAMES
    }
    result = {
        "desired_sha256": digest(desired),
        "config_sha256": digest(client.config),
        "dictionary_sha256": digest(dictionary),
        "snapshot_sha256": digest(snapshots),
        "changes": changes,
        "counts": {
            r: {"create": len(c["create"]), "update": len(c["update"])} for r, c in changes.items()
        },
    }
    save_json(run / "feishu-plan.json", result)
    return result["counts"]


def apply(client, run: Path, dictionary_path: Path | None = None):
    planned = json.loads((run / "feishu-plan.json").read_text())
    desired = json.loads((run / "desired.json").read_text())
    dictionary = load_dictionary(dictionary_path)
    if (
        planned["desired_sha256"] != digest(desired)
        or planned["config_sha256"] != digest(client.config)
        or planned["dictionary_sha256"] != digest(dictionary)
    ):
        raise ValueError("Plan input changed; generate and review a fresh plan")
    client.validate_schemas(dictionary)
    snapshots = snapshots_for(client, desired)
    if digest(snapshots) != planned["snapshot_sha256"]:
        raise ValueError("Target records changed after plan; re-plan")
    result = {}
    for role in TABLE_NAMES:
        rows = resolved_rows(role, desired, snapshots)
        if not rows:
            result[role] = {"created": 0, "updated": 0}
            continue
        if any(any("pending_" in str(v) for v in row["fields"].values()) for row in rows):
            raise ValueError(f"Unresolved relation: {role}")
        changes = diff_records(rows, snapshots[role], KEY_FIELDS[role])
        table_args = ["--table-id", client.config["tables"][role]]
        for offset in range(0, len(changes["create"]), 100):
            client.call(
                "+record-batch-create",
                table_args,
                {"create_records": changes["create"][offset : offset + 100]},
            )
            time.sleep(0.5)
        updates = list(changes["update"].items())
        for offset in range(0, len(updates), 100):
            client.call(
                "+record-batch-update",
                table_args,
                {"update_records": dict(updates[offset : offset + 100])},
            )
            time.sleep(0.5)
        for attempt in range(8):
            snapshots[role] = client.records(role)
            remaining = diff_records(rows, snapshots[role], KEY_FIELDS[role])
            if not remaining["create"] and not remaining["update"]:
                break
            if attempt == 7:
                raise ValueError(f"Read-back mismatch: {role}; no automatic duplicate writes")
            time.sleep(min(0.5 * 2**attempt, 2))
        result[role] = {"created": len(changes["create"]), "updated": len(changes["update"])}
    save_json(run / "feishu-apply.json", result)
    verify(client, run)
    return result


def verify(client, run: Path):
    desired = json.loads((run / "desired.json").read_text())
    snapshots = snapshots_for(client, desired)
    checked = 0
    for role in TABLE_NAMES:
        rows = resolved_rows(role, desired, snapshots)
        changes = diff_records(rows, snapshots[role], KEY_FIELDS[role])
        if changes["create"] or changes["update"]:
            save_json(run / "feishu-verification-failure.json", {"role": role, "changes": changes})
            raise ValueError(f"Final verification failed: {role}")
        checked += sum(len(row["fields"]) for row in rows)
    reverse_links = verify_relations(
        desired, snapshots, client.config.get("equipment_reverse_field", "参数扩展")
    )
    result = {
        "verified_cells": checked,
        "differences": 0,
        "verified_reverse_links": reverse_links,
        "records": {role: len(desired[role]) for role in TABLE_NAMES},
    }
    save_json(run / "feishu-verification.json", result)
    return result


def verify_relations(desired, snapshots, equipment_reverse_field="参数扩展"):
    models = record_index(snapshots["models"], KEY_FIELDS["models"])
    trims = record_index(snapshots["trims"], KEY_FIELDS["trims"])
    equipment = record_index(snapshots["equipment"], KEY_FIELDS["equipment"])
    checked = 0
    for row in desired["trims"]:
        parent = models[row["model_key"]]
        actual_ids = {r["id"] for r in parent["fields"].get("配置列表") or []}
        if trims[row["key"]]["record_id"] not in actual_ids:
            raise ValueError(f"Reverse model/trim relation missing: {row['key']}")
        checked += 1
    for row in desired["equipment"]:
        parent = trims[row["trim_key"]]
        actual_ids = {r["id"] for r in parent["fields"].get(equipment_reverse_field) or []}
        if equipment[row["key"]]["record_id"] not in actual_ids:
            raise ValueError(f"Reverse trim/equipment relation missing: {row['key']}")
        checked += 1
    return checked
