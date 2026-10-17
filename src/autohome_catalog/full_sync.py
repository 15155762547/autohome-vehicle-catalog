"""Prepare source-complete, column-budgeted batches using existing plan/apply/verify."""

import json
from pathlib import Path

from .export import EQUIPMENT_FIELDS, expand_packet, key_of, load_dictionary, verify_packet_sources
from .storage import assert_external, save_json


def prepare_batches(root: Path):
    root = assert_external(root)
    verification = json.loads((root / "full-export-verification.json").read_text())
    if verification["missing_series"] or verification["missing_specs"]:
        raise ValueError("Full source export is incomplete")
    catalog = json.loads((root / "full-field-catalog.json").read_text())
    original = load_dictionary()
    known = {key_of(f): f for f in original}
    new = []
    for f in catalog:
        key = (f["group"], f["section"], f["item_id"], f["name"])
        if key not in known:
            new.append(
                {
                    **{k: v for k, v in f.items() if k != "known"},
                    "unit": "",
                    "table": "trims" if f["group"] == "参数信息" else "equipment",
                    "kind": "text",
                }
            )
    main_fields = original + [f for f in new if f["table"] == "trims"]
    additions = [f for f in new if f["table"] == "equipment"]
    definitions = {"main": main_fields}
    width = 300 - len(EQUIPMENT_FIELDS) - 1
    for offset in range(0, len(additions), width):
        definitions[f"extension{offset // width + 2}"] = additions[offset : offset + width]
    full_dictionary = original + new
    desired = {name: {"models": [], "trims": [], "equipment": []} for name in definitions}
    columns = {
        name: {f["column"] for f in fs if f["table"] == "equipment"}
        for name, fs in definitions.items()
    }
    audit = []
    for packet_path in sorted((root / "series").glob("*.json")):
        packet = json.loads(packet_path.read_text())
        verify_packet_sources(root, packet)
        expanded, issues = expand_packet(packet, full_dictionary)
        if any(i["kind"] != "numeric_condition" for i in issues):
            raise ValueError("Unknown field in the supposedly complete field catalog")
        audit.extend(issues)
        desired["main"]["models"].extend(expanded["models"])
        desired["main"]["trims"].extend(expanded["trims"])
        for row in expanded["equipment"]:
            for name in definitions:
                params = {k: v for k, v in row["fields"].items() if k in columns[name]}
                if name != "main" and all(v in (None, "") for v in params.values()):
                    continue
                meta = {k: row["fields"][k] for k in EQUIPMENT_FIELDS}
                desired[name]["equipment"].append({**row, "fields": {**meta, **params}})
    if (
        len(desired["main"]["models"]) != verification["models"]
        or len(desired["main"]["trims"]) != verification["trims"]
    ):
        raise ValueError("Prepared record counts differ from the verified full source export")
    target = root / "feishu-batches"
    summaries = []
    for name, fs in definitions.items():
        directory = target / name
        save_json(
            directory / "fields.json",
            {"version": 3, "basis": "Verified full manifest snapshot", "fields": fs},
        )
        load_dictionary(
            directory / "fields.json"
        )  # Enforce source identities and the 300-column budget.
        save_json(directory / "desired.json", desired[name])
        summary = {
            "batch": name,
            "fields": len(fs),
            "records": {r: len(v) for r, v in desired[name].items()},
        }
        save_json(directory / "batch-summary.json", summary)
        summaries.append(summary)
    save_json(
        root / "numeric-condition-audit.json",
        {
            "handling": "数值列留空；完整原文保留于对应文本列；不强转区间、比例、准高压或选装条件",
            "issues": audit,
        },
    )
    save_json(root / "feishu-batch-plan.json", summaries)
    return summaries


def sync_batches(client, root: Path):
    import copy
    import time

    from .feishu import Client, apply, digest, plan, sync_schema, table_schemas

    root = assert_external(root)
    batches = json.loads((root / "feishu-batch-plan.json").read_text())
    names = [
        f"配置参数扩展{b['batch'].removeprefix('extension')}"
        for b in batches
        if b["batch"] != "main"
    ]
    if not (root / "feishu-config-before-full.json").exists():
        save_json(root / "feishu-config-before-full.json", client.config)
        save_json(
            root / "feishu-records-before-full.json",
            {role: client.records(role) for role in ("models", "trims", "equipment")},
        )
        save_json(
            root / "feishu-schemas-before-full.json",
            {
                role: client.call("+field-list", ["--table-id", tid])["fields"]
                for role, tid in client.config["tables"].items()
            },
        )
    client.config["extension_table_names"] = names
    client.config.setdefault("extension_tables", {})
    save_json(client.config_path, client.config)
    configs = {"main": client.config_path}
    for batch in batches:
        name = batch["batch"]
        if name == "main":
            continue
        number = name.removeprefix("extension")
        table_name = "配置参数扩展" + number
        directory = root / "feishu-batches" / name
        dictionary = load_dictionary(directory / "fields.json")
        fields = table_schemas(dictionary, client.config["tables"])["equipment"]
        link = next(f for f in fields if f["name"] == "对应配置")
        link["bidirectional_link_field_name"] = "参数扩展" + number
        table_id = client.config["extension_tables"].get(name)
        existing = [t for t in client.call("+table-list")["tables"] if t["name"] == table_name]
        if table_id:
            if len(existing) != 1 or existing[0]["id"] != table_id:
                raise ValueError("Extension identity/name changed")
        else:
            if existing:
                raise ValueError(
                    "Unrecognized extension table exists; inspect the creation receipt"
                )
            args = ["--name", table_name, "--fields", json.dumps(fields, ensure_ascii=False)]
            client.call("+table-create", args, dry_run=True)
            client.call("+table-create", args)
            for attempt in range(10):
                existing = [
                    t for t in client.call("+table-list")["tables"] if t["name"] == table_name
                ]
                if len(existing) == 1:
                    break
                time.sleep(1)
            if len(existing) != 1:
                raise ValueError("Created table is not visible; inspect receipt before retrying")
            table_id = existing[0]["id"]
            client.config["extension_tables"][name] = table_id
            save_json(client.config_path, client.config)
        config = copy.deepcopy(client.config)
        config["tables"]["equipment"] = table_id
        config["equipment_reverse_field"] = "参数扩展" + number
        path = directory / "feishu-config.json"
        save_json(path, config)
        configs[name] = path
        print(f"{name}: schema ready ({len(fields)} columns)", flush=True)
        time.sleep(1)
    results = []
    checkpoint_path = root / "feishu-full-progress.json"
    completed = (
        {r["batch"]: r for r in json.loads(checkpoint_path.read_text())}
        if checkpoint_path.exists()
        else {}
    )
    catalog = json.loads((root / "full-field-catalog.json").read_text())
    conditions = json.loads((root / "numeric-condition-audit.json").read_text())["issues"]
    condition_keys = {tuple(i["field"]) for i in conditions}
    for batch in batches:
        name = batch["batch"]
        directory = root / "feishu-batches" / name
        worker = Client(configs[name])
        dictionary = directory / "fields.json"
        if name == "main":
            print("main: adding audited fields", flush=True)
            sync_schema(worker, dictionary)
        else:
            worker.validate_schemas(load_dictionary(dictionary))
        if name in completed:
            recorded = json.loads((directory / "feishu-plan.json").read_text())
            if (
                recorded["desired_sha256"]
                != digest(json.loads((directory / "desired.json").read_text()))
                or recorded["dictionary_sha256"] != digest(load_dictionary(dictionary))
                or recorded["config_sha256"] != digest(worker.config)
            ):
                raise ValueError("Verified checkpoint inputs changed; inspect before resuming")
            results.append(completed[name])
            print(f"{name}: continuing after verified zero-change checkpoint", flush=True)
            continue
        for attempt in range(4):
            counts = plan(worker, directory, dictionary)
            print(f"{name}: plan {counts}", flush=True)
            try:
                applied = apply(worker, directory, dictionary)
                break
            except RuntimeError:
                failure = getattr(worker, "last_failure", {})
                if failure.get("code") not in (800100001, 800004135) or attempt == 3:
                    raise
                print(
                    f"{name}: transient service failure; waiting, reading existing IDs and replanning",
                    flush=True,
                )
                time.sleep(3)
        else:
            raise ValueError("No verified batch application")
        print(f"{name}: applied and read-back verified {applied}", flush=True)
        repeat = plan(worker, directory, dictionary)
        if any(v["create"] or v["update"] for v in repeat.values()):
            raise ValueError("Same-snapshot replay is not idempotent")
        visible = configure_view(worker, directory, catalog, condition_keys)
        result = {
            "batch": name,
            "applied": applied,
            "repeat_plan": repeat,
            "visible_fields": visible,
        }
        results.append(result)
        save_json(root / "feishu-full-progress.json", results)
    save_json(root / "feishu-full-verification.json", results)
    return results


def configure_view(client, directory, catalog, condition_keys):
    import time

    from .export import raw_column

    dictionary = load_dictionary(directory / "fields.json")
    rank = {(f["group"], f["section"], f["item_id"], f["name"]): i for i, f in enumerate(catalog)}
    roles = ("models", "trims", "equipment") if directory.name == "main" else ("equipment",)
    result = {}
    for role in roles:
        table = client.config["tables"][role]
        args = ["--table-id", table]
        views = client.call("+view-list", args)["views"]
        grids = [v for v in views if v["type"] == "grid" and v["name"] == "表格"]
        if len(grids) != 1:
            raise ValueError("Default grid view is not unique")
        args += ["--view-id", grids[0]["id"]]
        before = client.call("+view-get-visible-fields", args)["visible_fields"]
        save_json(directory / f"{role}-view-before.json", before)
        if role == "models":
            expected = ["车系名称", "model_id"] + [
                n for n in before if n not in ("车系名称", "model_id")
            ]
        else:
            parameters = sorted(
                (f for f in dictionary if f["table"] == role and key_of(f) in rank),
                key=lambda f: rank[key_of(f)],
            )
            expected = ["配置名称", "model_id"]
            for f in parameters:
                expected.append(f["column"])
                if f["kind"] == "number" and key_of(f) in condition_keys:
                    expected.append(raw_column(f))
            auxiliary = [
                n
                for n in before
                if n not in expected
                and not n.startswith("原文｜")
                and n not in {f["column"] for f in dictionary}
            ]
            expected += auxiliary
        save_json(directory / f"{role}-view-request.json", expected)
        client.call("+view-set-visible-fields", args, {"visible_fields": expected}, dry_run=True)
        client.call("+view-set-visible-fields", args, {"visible_fields": expected})
        for attempt in range(8):
            actual = client.call("+view-get-visible-fields", args)["visible_fields"]
            if actual == expected:
                break
            time.sleep(1 + attempt)
        if actual != expected:
            raise ValueError("View order read-back mismatch")
        save_json(directory / f"{role}-view-after.json", actual)
        result[role] = len(expected)
    return result
