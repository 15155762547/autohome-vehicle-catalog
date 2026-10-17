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
    if command == "+table-create" and args[args.index("--name") + 1] == "车型图片":
        return
    tid = args[args.index("--table-id") + 1] if "--table-id" in args else None
    if (
        tid == config["tables"].get("images")
        and tid
        and command in {"+record-batch-create", "+record-batch-update", "+view-set-visible-fields"}
    ):
        return
    if tid == config["tables"]["models"] and command == "+view-set-visible-fields":
        return
    raise ValueError(f"Operation outside image table scope: {command}")


def url_lines(value):
    return [
        re.sub(r"^\[[^\]]*\]\((https://[^\s]+)\)$", r"\1", line.strip())
        for line in (value or "").splitlines()
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
        if p["http_status"] != 200 or not p["content_type"].startswith("image/"):
            raise ValueError("Unverified image in usable list")
        if p["spec_id"] not in t["specs"]:
            raise ValueError("Photo specification outside target")
    return {
        "车型": t["name"],
        "model_id": t["model_id"],
        "品牌": t["brand"],
        "动力": t["power"],
        "年款": str(t["year"]),
        "所属车系": [{"id": t["model_record"]}],
        "官图URL清单": "\n".join(p["url"] for p in images),
        "图片数": len(images),
        "检查结果": result["status"],
        "说明": result["note"]
        + ("；" + "；".join(p["reason"] for p in result["problems"]) if result["problems"] else ""),
        "来源页": "\n".join(dict.fromkeys(p["source_page"] for p in images))
        or f"https://www.autohome.com.cn/cars/imglist-x-x-{t['series_id']}-x-53-x-x-x-x-1.html",
        "检查时间": result["checked_at"],
    }


def normalize(records):
    for row in records:
        for key in ["官图URL清单", "来源页"]:
            if key in row["fields"]:
                row["fields"][key] = "\n".join(url_lines(row["fields"][key]))
    return records


def sync(config_path, root):
    root = assert_external(root)
    client = Client(config_path)
    client.config["_image_operation"] = True
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
            "官图URL清单",
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
    current = normalize(read_records(client, tid, "model_id"))
    save_json(backup / "images.json", current)
    desired = []
    for result in all_results(root):
        desired.append({"key": result["target"]["model_id"], "fields": fields(result)})
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
    models = record_index(client.records("models"), "来源车系ID")
    targets_by_id = {
        t["model_id"]: t for t in json.loads((root / "targets.json").read_text())["targets"]
    }
    for row in desired:
        result = targets_by_id[row["key"]]
        linked = models[str(result["series_id"])]["fields"].get("官图组") or []
        if by_id[row["key"]]["record_id"] not in {r["id"] for r in linked}:
            raise ValueError("Missing reverse model image relation")
    # Only image association and hidden card fields may differ in the original model table.
    before = record_index(before_models, "来源车系ID")
    for key, row in models.items():
        for name, value in before[key]["fields"].items():
            if name != "官图组" and not same_value(value, row["fields"].get(name)):
                raise ValueError(f"Model field unexpectedly changed: {key}/{name}")
    for view in views:
        if view.get("name") != "表格":
            continue
        args = ["--table-id", model_table, "--view-id", view["id"]]
        visible = client.call("+view-get-visible-fields", args)["visible_fields"]
        visible = [x for x in visible if x not in {"车系图片URL", "图片来源页", "官图组"}]
        visible.insert(min(2, len(visible)), "官图组")
        set_visible(client, args, visible)
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
