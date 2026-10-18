"""Idempotent image-only writes to the experimental Base, with backup/read-back."""

import json
import re
import time

from .feishu import Client, diff_records, record_index, same_value
from .human_layout import read_records, set_visible
from .images import all_results, fingerprint, image_url, now
from .storage import assert_external, save_json


def assert_image_operation(config, command, args):
    if command in {
        "+table-list",
        "+field-list",
        "+record-list",
        "+view-list",
        "+view-get-visible-fields",
    }:
        return
    if config.get("image_storage") == "models_json":
        tid = args[args.index("--table-id") + 1] if "--table-id" in args else None
        if tid == config["tables"]["models"] and command == "+record-batch-update":
            return
        raise ValueError(f"Merged image sync only updates model image columns: {command}")
    if command == "+table-create" and args[args.index("--name") + 1] == "车型图片":
        return
    tid = args[args.index("--table-id") + 1] if "--table-id" in args else None
    if (
        tid == config["tables"].get("images")
        and tid
        and command in {"+record-batch-create", "+record-batch-update", "+view-set-visible-fields"}
    ):
        return
    if tid == config["tables"].get("images") and tid:
        if command == "+field-create":
            return
        if (
            command == "+field-update"
            and "--field-id" in args
            and args[args.index("--field-id") + 1] == "官图URL清单"
        ):
            return
    if tid == config["tables"]["models"] and command == "+view-set-visible-fields":
        return
    raise ValueError(f"Operation outside image table scope: {command}")


def url_lines(value):
    text = (value or "").strip()
    if not text:
        return []
    if text.startswith("[") and not re.match(r'^\[[^\[\]\n"]*\]\(https?://', text):
        items = json.loads(text)
        if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
            raise ValueError("Image JSON must be a string array")
        urls = [re.sub(r"^\[[^\]]*\]\((https://[^\s]+)\)$", r"\1", item) for item in items]
        if not all(re.fullmatch(r"https://[^\s\[\]()]+", url) for url in urls):
            raise ValueError("JSON image list contains a non-URL value")
        return urls
    return [
        re.sub(r"^\[[^\]]*\]\((https://[^\s]+)\)$", r"\1", line.strip())
        for line in text.splitlines()
        if line.strip()
    ]


def fields(result):
    t = result["target"]
    if result["target_sha256"] != fingerprint(t):
        raise ValueError("Target evidence changed")
    images = result["images"]
    if len(images) > 15 or len({p["url"] for p in images}) != len(images):
        raise ValueError("Duplicate or excess URLs")
    for p in images:
        image_url(p["url"])
        source_only = (
            result.get("policy") == "source-urls-official-first-v1"
            and p.get("verification") == "source_page_only"
        )
        if not source_only and (
            p.get("http_status") != 200 or not p.get("content_type", "").startswith("image/")
        ):
            raise ValueError("Unverified image in usable list")
        if p["spec_id"] not in t["specs"] and not (
            source_only and p.get("scope") == "series" and p["spec_id"] is None
        ):
            raise ValueError("Photo specification outside target")
    return {
        "车型": t["name"],
        "model_id": t["model_id"],
        "品牌": t["brand"],
        "动力": t["power"],
        "年款": ""
        if images and all(p.get("scope") == "series" for p in images)
        else str(t["year"]),
        "所属车系": [{"id": t["model_record"]}],
        "图片URL清单": json.dumps([p["url"] for p in images], ensure_ascii=False),
        "图片数": len(images),
        "图片类别": "、".join(dict.fromkeys(p.get("category", "官图") for p in images)),
        "验证方式": "源站提供URL，未直连验证"
        if any(p.get("verification") == "source_page_only" for p in images)
        else "HTTP200及图片类型验证"
        if images
        else "尚无图片URL",
        "检查结果": result["status"],
        "说明": result["note"]
        + ("；" + "；".join(p["reason"] for p in result["problems"]) if result["problems"] else ""),
        "来源页": "\n".join(dict.fromkeys(p["source_page"] for p in images))
        or f"https://www.autohome.com.cn/cars/imglist-x-x-{t['series_id']}-x-53-x-x-x-x-1.html",
        "检查时间": result["checked_at"],
    }


def normalize(records):
    for row in records:
        for key in ["官图URL清单", "图片URL清单", "来源页"]:
            if key in row["fields"]:
                row["fields"][key] = (
                    "\n".join(url_lines(row["fields"][key]))
                    if key == "来源页"
                    else json.dumps(url_lines(row["fields"][key]), ensure_ascii=False)
                )
    return records


def sync(config_path, root):
    root = assert_external(root)
    client = Client(config_path)
    client.config["_image_operation"] = True
    if client.config.get("image_storage") == "models_json":
        from .image_merge import sync_models

        return sync_models(client, root)
    tables = client.call("+table-list")["tables"]
    before_models = client.records("models")
    backup = root / "backups" / now().replace(":", "-")
    save_json(backup / "tables.json", tables)
    save_json(backup / "models.json", before_models)
    model_table = client.config["tables"]["models"]
    views = client.call("+view-list", ["--table-id", model_table])["views"]
    for view in views:
        args = ["--table-id", model_table, "--view-id", view["id"]]
        save_json(
            backup / ("view-" + view["id"] + ".json"), client.call("+view-get-visible-fields", args)
        )
    tid = client.config["tables"].get("images")
    if not tid:
        if any(t["name"] == "车型图片" for t in tables):
            raise ValueError(
                "Unregistered image table exists; inspect creation receipt before retry"
            )
        names = [
            "车型",
            "model_id",
            "品牌",
            "动力",
            "年款",
            "所属车系",
            "图片URL清单",
            "图片数",
            "检查结果",
            "说明",
            "来源页",
            "检查时间",
        ]
        schema = [{"name": n, "type": "text"} for n in names]
        schema[5] = {
            "name": "所属车系",
            "type": "link",
            "link_table": model_table,
            "bidirectional": True,
            "bidirectional_link_field_name": "官图组",
        }
        schema[7] = {"name": "图片数", "type": "number", "style": {"type": "plain", "precision": 0}}
        result = client.call(
            "+table-create",
            ["--name", "车型图片", "--fields", json.dumps(schema, ensure_ascii=False)],
        )
        tid = (
            result["table"]["id"] if "table" in result else result.get("table_id", result.get("id"))
        )
        if not tid:
            raise ValueError("Inspect table creation receipt for table ID")
        client.config["tables"]["images"] = tid
        saved = dict(client.config)
        saved.pop("_image_operation")
        save_json(client.config_path, saved)
    schema = client.call("+field-list", ["--table-id", tid])["fields"]
    save_json(backup / "image-fields.json", schema)
    # Preserve the original image cells before renaming their field.
    save_json(backup / "image-records-before-schema.json", read_records(client, tid, "model_id"))
    if any(f["name"] == "官图URL清单" for f in schema):
        old = next(f for f in schema if f["name"] == "官图URL清单")
        definition = {k: v for k, v in old.items() if k != "id"}
        definition["name"] = "图片URL清单"
        client.call(
            "+field-update", ["--table-id", tid, "--field-id", "官图URL清单", "--yes"], definition
        )
    missing = [
        {"name": name, "type": "text"}
        for name in ["图片类别", "验证方式"]
        if not any(f["name"] == name for f in schema)
    ]
    if missing:
        client.call("+field-create", ["--table-id", tid], missing)
    current = normalize(read_records(client, tid, "model_id"))
    save_json(backup / "images.json", current)
    desired = []
    model_key = client.config.get("key_fields", {}).get("models", "来源车系ID")
    model_index = record_index(before_models, model_key)
    relation = client.config.get("model_relation_field", "所属车系")
    reverse = client.config.get("models_image_field", "官图组")
    for result in all_results(root):
        target = result["target"]
        parent = model_index[
            target["model_id"] if model_key == "model_id" else str(target["series_id"])
        ]
        values = fields(result)
        values.pop("所属车系")
        values[relation] = [{"id": parent["record_id"]}]
        if model_key == "model_id":
            values["车型"] = parent["fields"]["车系名称"]
        desired.append({"key": target["model_id"], "fields": values})
    changes = diff_records(desired, current, "model_id")
    save_json(backup / "write-plan.json", changes)
    for offset in range(0, len(changes["create"]), 200):
        client.call(
            "+record-batch-create",
            ["--table-id", tid],
            {"create_records": changes["create"][offset : offset + 200]},
        )
    updates = list(changes["update"].items())
    for offset in range(0, len(updates), 100):
        client.call(
            "+record-batch-update",
            ["--table-id", tid],
            {"update_records": dict(updates[offset : offset + 100])},
        )
    for attempt in range(6):
        actual = normalize(read_records(client, tid, "model_id"))
        remaining = diff_records(desired, actual, "model_id")
        if not remaining["create"] and not remaining["update"]:
            break
        time.sleep(1 + attempt)
    if remaining["create"] or remaining["update"]:
        save_json(root / "readback-differences.json", remaining)
        raise ValueError("Image table differs after read-back")
    by_id = record_index(actual, "model_id")
    models = record_index(client.records("models"), model_key)
    targets_by_id = {
        t["model_id"]: t for t in json.loads((root / "targets.json").read_text())["targets"]
    }
    for row in desired:
        result = targets_by_id[row["key"]]
        parent_key = row["key"] if model_key == "model_id" else str(result["series_id"])
        linked = models[parent_key]["fields"].get(reverse) or []
        if by_id[row["key"]]["record_id"] not in {r["id"] for r in linked}:
            raise ValueError("Missing reverse model image relation")
    # Only image association and hidden card fields may differ in the original model table.
    before = record_index(before_models, model_key)
    for key, row in models.items():
        for name, value in before[key]["fields"].items():
            if name != reverse and not same_value(value, row["fields"].get(name)):
                raise ValueError(f"Model field unexpectedly changed: {key}/{name}")
    for view in views:
        if view.get("name") != "表格":
            continue
        args = ["--table-id", model_table, "--view-id", view["id"]]
        visible = client.call("+view-get-visible-fields", args)["visible_fields"]
        visible = [x for x in visible if x not in {"车系图片URL", "图片来源页"}]
        if model_key != "model_id":
            visible = [x for x in visible if x != reverse]
            visible.insert(min(2, len(visible)), reverse)
        set_visible(client, args, visible)
    image_views = client.call("+view-list", ["--table-id", tid])["views"]
    for view in image_views:
        if view.get("name") == "表格":
            set_visible(
                client,
                ["--table-id", tid, "--view-id", view["id"]],
                [
                    "车型",
                    "model_id",
                    "品牌",
                    "动力",
                    "年款",
                    "图片数",
                    "图片类别",
                    "图片URL清单",
                    "验证方式",
                    relation,
                    "检查结果",
                    "说明",
                    "来源页",
                    "检查时间",
                ],
            )
    summary = {
        "verified_rows": len(desired),
        "table_id": tid,
        "created": len(changes["create"]),
        "updated": len(changes["update"]),
        "reverse_links_verified": True,
        "model_values_unchanged": True,
        "time": now(),
    }
    save_json(root / "feishu-verification.json", summary)
    return summary
