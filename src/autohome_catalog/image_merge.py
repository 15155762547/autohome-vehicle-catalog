"""Merge the one-to-one image rows into existing model records, retaining JSON URLs."""

import argparse
import json
from pathlib import Path

from .base_utils import read_records, set_visible, verify_fields, write_updates
from .feishu import Client, diff_records, record_index
from .image_sync import fields, url_lines
from .images import all_results, now
from .storage import assert_external, save_json

IMAGE_FIELDS = {
    "年款": "图片年款",
    "图片URL清单": "图片URL清单",
    "图片数": "图片数",
    "图片类别": "图片类别",
    "验证方式": "图片验证方式",
    "检查结果": "图片检查结果",
    "说明": "图片说明",
    "来源页": "图片采集来源页",
    "检查时间": "图片检查时间",
}
FOLDER = "历史备份｜图片合并20261018"


def guard(config, command, args):
    if command in {
        "+table-list",
        "+field-list",
        "+record-list",
        "+view-list",
        "+view-get-visible-fields",
        "+base-block-list",
    }:
        return
    state = config["_image_merge"]
    tid = args[args.index("--table-id") + 1] if "--table-id" in args else None
    if tid == config["tables"]["models"] and command in {
        "+field-create",
        "+record-batch-update",
        "+view-set-visible-fields",
    }:
        return
    if command == "+base-block-create" and args == ["--type", "folder", "--name", FOLDER]:
        return
    if command == "+base-block-rename" and args == [
        "--block-id",
        state["images_id"],
        "--name",
        "车型图片（合并备份20261018）",
    ]:
        return
    if command == "+base-block-move" and args == [
        "--block-id",
        state["images_id"],
        "--parent-id",
        state["folder"],
    ]:
        return
    raise ValueError(f"Outside image-to-model merge: {command}")


def projected(source):
    # No identity, model name or configuration relationship can enter this patch.
    result = {dest: source.get(origin) for origin, dest in IMAGE_FIELDS.items()}
    urls = url_lines(source["图片URL清单"])
    if len(urls) != source["图片数"] or len(set(urls)) != len(urls):
        raise ValueError("Image list count or uniqueness differs")
    result["图片URL清单"] = json.dumps(urls, ensure_ascii=False)
    result["图片数"] = len(urls)
    return result


def canonical(values):
    result = {name: values.get(name) for name in IMAGE_FIELDS.values()}
    result["图片URL清单"] = json.dumps(url_lines(values.get("图片URL清单")), ensure_ascii=False)
    result["图片采集来源页"] = "\n".join(url_lines(values.get("图片采集来源页")))
    for name in IMAGE_FIELDS.values():
        if name != "图片数" and result[name] is None:
            result[name] = ""
    return result


def check(expected, actual):
    if json.loads(expected["图片URL清单"]) != url_lines(actual.get("图片URL清单")):
        raise ValueError("Image URLs or their order changed")
    verify_fields(canonical(expected), canonical(actual))


def merge(config, root):
    root = assert_external(root)
    root.mkdir(parents=True, exist_ok=True)
    client = Client(config)
    if client.config.get("image_storage") == "models_json":
        return {"already_merged": True, **json.loads((root / "verification.json").read_text())}
    tid = client.config["tables"]["models"]
    image_tid = client.config["tables"]["images"]
    state_path = root / "state.json"
    state = (
        json.loads(state_path.read_text())
        if state_path.exists()
        else {"images_id": image_tid, "folder": None}
    )
    client.config["_image_merge"] = state
    before_path = root / "models-before.json"
    if not before_path.exists():
        meta = {t["id"]: t for t in client.call("+table-list")["tables"]}
        schemas = {
            role: client.call("+field-list", ["--table-id", table])["fields"]
            for role, table in [("models", tid), ("images", image_tid)]
        }
        models = record_index(read_records(client, tid, "model_id"), "model_id")
        images = record_index(read_records(client, image_tid, "model_id"), "model_id")
        after = {t["id"]: t for t in client.call("+table-list")["tables"]}
        if any(meta[t]["rev"] != after[t]["rev"] for t in [tid, image_tid]):
            raise ValueError("Tables changed during backup")
        save_json(
            root / "config-before.json",
            {k: v for k, v in client.config.items() if k != "_image_merge"},
        )
        save_json(root / "schemas-before.json", schemas)
        save_json(root / "images-before.json", images)
        save_json(root / "tables-before.json", meta)
        save_json(before_path, models)
        save_json(state_path, state)
    models = json.loads(before_path.read_text())
    images = json.loads((root / "images-before.json").read_text())
    schema = json.loads((root / "schemas-before.json").read_text())
    if models.keys() != images.keys():
        raise ValueError("Model and image IDs are not one-to-one")
    source_url_fields = [f["name"] for f in schema["images"] if f["name"].startswith("图片URL清单")]
    if len(source_url_fields) != 1:
        raise ValueError("Image URL field ambiguous")
    parents = [f["name"] for f in schema["images"] if f.get("link_table") == tid]
    if len(parents) != 1:
        raise ValueError("Image parent field ambiguous")
    desired = {}
    for mid, row in images.items():
        if {x["id"] for x in row["fields"].get(parents[0]) or []} != {models[mid]["record_id"]}:
            raise ValueError("Existing image parent does not match model_id")
        source = {**row["fields"], "图片URL清单": row["fields"][source_url_fields[0]]}
        desired[mid] = projected(source)
    save_json(root / "write-plan.json", desired)
    actual_schema = {
        f["name"]: f for f in client.call("+field-list", ["--table-id", tid])["fields"]
    }
    additions = []
    for name in IMAGE_FIELDS.values():
        kind = "number" if name == "图片数" else "text"
        if name in actual_schema:
            if actual_schema[name]["type"] != kind or name in {f["name"] for f in schema["models"]}:
                raise ValueError("Existing model field collision; no overwrite")
        else:
            field = {"name": name, "type": kind}
            if name == "图片URL清单":
                field["description"] = "JSON数组字符串，每项为纯图片URL；顺序与源图片列表一致。"
            additions.append(field)
    if len(actual_schema) + len(additions) > 300:
        raise ValueError("Model table field limit exceeded")
    if additions:
        client.call("+field-create", ["--table-id", tid], additions)
    # Read after field creation so fresh fields are visible and partial writes are reconciled.
    current = record_index(read_records(client, tid, "model_id"), "model_id")
    updates = {}
    for mid, expected in desired.items():
        try:
            check(expected, current[mid]["fields"])
        except (ValueError, TypeError):
            updates[current[mid]["record_id"]] = expected
    write_updates(client, tid, updates)
    actual = record_index(read_records(client, tid, "model_id"), "model_id")
    if actual.keys() != models.keys():
        raise ValueError("Model identities changed")
    for mid, row in actual.items():
        if row["record_id"] != models[mid]["record_id"]:
            raise ValueError("Model record ID changed")
        verify_fields(models[mid]["fields"], row["fields"])
        check(desired[mid], row["fields"])
    save_json(root / "models-after.json", actual)
    meta = {t["id"]: t for t in client.call("+table-list")["tables"]}
    before_meta = json.loads((root / "tables-before.json").read_text())
    # Parameters and source image rows are never written by this migration.
    for table in [
        image_tid,
        client.config["tables"]["trims"],
        *client.config["parameter_extension_tables"],
        *client.config["detail_tables"],
    ]:
        if meta[table]["rev"] != before_meta[table]["rev"]:
            raise ValueError("Source image or parameter table changed during merge")
    for view in client.call("+view-list", ["--table-id", tid])["views"]:
        args = ["--table-id", tid, "--view-id", view["id"]]
        visible = client.call("+view-get-visible-fields", args)["visible_fields"]
        save_json(root / ("view-before-" + view["id"] + ".json"), visible)
        visible = [
            n
            for n in visible
            if n not in {"图片组", "官图组", "车系图片URL", "图片来源页", *IMAGE_FIELDS.values()}
        ]
        set_visible(client, args, visible + ["图片URL清单", "图片数"])
    if state["folder"] is None:
        found = [b["id"] for b in client.call("+base-block-list")["blocks"] if b["name"] == FOLDER]
        if not found:
            client.call("+base-block-create", ["--type", "folder", "--name", FOLDER])
            found = [
                b["id"] for b in client.call("+base-block-list")["blocks"] if b["name"] == FOLDER
            ]
        if len(found) != 1:
            raise ValueError("Archive folder ambiguous")
        state["folder"] = found[0]
        save_json(state_path, state)
    client.call(
        "+base-block-rename", ["--block-id", image_tid, "--name", "车型图片（合并备份20261018）"]
    )
    client.call("+base-block-move", ["--block-id", image_tid, "--parent-id", state["folder"]])
    archived = client.call("+base-block-list", ["--parent-id", state["folder"]])["blocks"]
    if image_tid not in {b["id"] for b in archived}:
        raise ValueError("Archive move unconfirmed")
    saved = {k: v for k, v in client.config.items() if k != "_image_merge"}
    saved["tables"] = {k: v for k, v in saved["tables"].items() if k != "images"}
    saved.update(
        image_storage="models_json", archived_images_table=image_tid, images_merge_run=root.name
    )
    result = {
        "model_rows": len(actual),
        "url_count": sum(v["图片数"] for v in desired.values()),
        "model_table": tid,
        "archived_image_table": image_tid,
        "added_fields": list(IMAGE_FIELDS.values()),
        "all_urls_order_and_metadata_verified": True,
        "old_model_values_and_ids_preserved": True,
        "parameters_unchanged": True,
    }
    save_json(root / "verification.json", result)
    save_json(config, saved)
    return result


def sync_models(client, root):
    """Only image columns may be updated; existing model IDs must already exist."""
    tid = client.config["tables"]["models"]
    before = record_index(read_records(client, tid, "model_id"), "model_id")
    active = set(IMAGE_FIELDS.values()) & {
        name for row in before.values() for name in row["fields"]
    }

    def existing_columns(values):
        return {name: value for name, value in canonical(values).items() if name in active}

    backup = root / "backups" / ("models-images-" + now().replace(":", "-"))
    save_json(backup / "models.json", before)
    desired = {
        r["target"]["model_id"]: existing_columns(projected(fields(r))) for r in all_results(root)
    }
    # Explicitly accepted historical replacements must survive later ordinary image syncs.
    from .historical_images import image_patch

    for path in (root / "historical-overrides").glob("*.json"):
        override = json.loads(path.read_text())
        mid = override["target"]["model_id"]
        if path.stem != mid or mid not in desired:
            raise ValueError("Historical image override identity mismatch")
        desired[mid] = existing_columns(image_patch(override))
    if not desired or desired.keys() - before.keys():
        raise ValueError("Image collection does not match existing model IDs")
    current = [
        {"record_id": row["record_id"], "fields": {"model_id": mid, **existing_columns(row["fields"])}}
        for mid, row in before.items()
    ]
    changes = diff_records(
        [
            {"key": mid, "fields": {"model_id": mid, **existing_columns(values)}}
            for mid, values in desired.items()
        ],
        current,
        "model_id",
    )
    if changes["create"]:
        raise ValueError("Image sync cannot create model records")
    if any(set(patch) - set(IMAGE_FIELDS.values()) for patch in changes["update"].values()):
        raise ValueError("Image sync attempted a non-image patch")
    save_json(backup / "write-plan.json", changes)
    write_updates(client, tid, changes["update"])
    actual = (
        record_index(read_records(client, tid, "model_id"), "model_id")
        if changes["update"]
        else before
    )
    if actual.keys() != before.keys():
        raise ValueError("Model identities changed during image sync")
    for mid, row in actual.items():
        if row["record_id"] != before[mid]["record_id"]:
            raise ValueError("Model record IDs changed")
        verify_fields(
            {n: v for n, v in before[mid]["fields"].items() if n not in IMAGE_FIELDS.values()},
            row["fields"],
        )
        if mid in desired:
            verify_fields(existing_columns(desired[mid]), existing_columns(row["fields"]))
    result = {
        "verified_rows": len(desired),
        "table_id": tid,
        "created": 0,
        "updated": len(changes["update"]),
        "model_values_unchanged": True,
        "image_storage": "models_json",
        "time": now(),
    }
    save_json(root / "feishu-verification.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(merge(args.config, args.run), ensure_ascii=False))
