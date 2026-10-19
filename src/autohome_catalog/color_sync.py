"""Back up, stage, verify and publish per-configuration color details through Lark CLI."""

import argparse
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .base_utils import read_records, set_visible, settled_fields, verify_fields, write_updates
from .category_tables import definition, link
from .colors import AVAILABILITY, BASE_COLORS, KEY, RIGHT, compile_colors, gallery_identity
from .feishu import Client, record_index, same_value
from .storage import assert_external, assert_run_allowed, save_json

FOLDER = "历史备份｜颜色明细20261019"
OLD_ENTRY = "颜色原表（存档）"
PENDING = "颜色明细（整理中）"
HIDDEN = {"原厂色名", "完整色值", "源颜色编号", KEY, "原始顺序", "单项原文JSON", "来源采集时间"}
READS = {
    "+table-list",
    "+field-list",
    "+record-list",
    "+view-list",
    "+view-get-visible-fields",
    "+view-get-filter",
    "+view-get-sort",
    "+view-get-group",
    "+base-block-list",
}


def guard(config, command, args):
    state = config["_colors"]

    def value(flag):
        return args[args.index(flag) + 1] if flag in args else None

    if command in READS:
        return
    tid = value("--table-id")
    owned = set(state["tables"].values())
    if command == "+table-create" and value("--name") in {
        PENDING,
        "外观颜色（整理中）",
        "内饰颜色（整理中）",
    }:
        return
    if command in {"+record-batch-create", "+view-set-visible-fields"} and tid in owned:
        return
    if command == "+record-batch-update" and tid in owned | {state["index"]}:
        return
    if command == "+field-create" and tid == state["index"]:
        return
    if command == "+field-update" and tid == state["index"]:
        if value("--field-id") in state.get("index_fields", []):
            return
    if command == "+view-set-visible-fields" and tid == state["index"]:
        return
    if command == "+base-block-create" and args == ["--type", "folder", "--name", FOLDER]:
        return
    if command in {"+base-block-rename", "+base-block-move"}:
        if value("--block-id") in owned | {state["source"]}:
            return
    raise ValueError(f"Outside color migration: {command}")


def read(client, tid, key):
    return record_index(read_records(client, tid, key), key)


def metadata(client, tid):
    fields = client.call("+field-list", ["--table-id", tid])["fields"]
    views = client.call("+view-list", ["--table-id", tid])["views"]
    detail = {}
    for view in views:
        args = ["--table-id", tid, "--view-id", view["id"]]
        detail[view["id"]] = {
            kind: client.call("+view-get-" + kind, args)
            for kind in ["visible-fields", "filter", "sort", "group"]
        }
    return {"fields": fields, "views": views, "detail": detail}


def client_state(config, root):
    client = Client(config)
    path = root / "state.json"
    if path.exists():
        state = json.loads(path.read_text())
    else:
        state = {
            "source": client.config["category_tables"]["颜色"],
            "index": client.config["tables"]["trims"],
            "tables": {},
            "index_fields": [],
        }
        save_json(path, state)
    client.config["_colors"] = state
    return client, state


def prepare(config, root):
    client, state = client_state(config, root)
    if not (root / "backup.json").exists():
        backup = {
            "config": json.loads(config.read_text()),
            "time": datetime.now().astimezone().isoformat(),
            "blocks": client.call("+base-block-list"),
            "color_meta": metadata(client, state["source"]),
            "index_meta": metadata(client, state["index"]),
            "colors": read(client, state["source"], "来源配置ID"),
            "index": read(client, state["index"], "来源配置ID"),
        }
        save_json(root / "backup.json", backup)
        (root / "backup.sha256").write_text(
            hashlib.sha256((root / "backup.json").read_bytes()).hexdigest()
        )
    backup = load_backup(root)
    expected, statuses, counts = compile_colors(backup["colors"], backup["index"])
    save_json(root / "expected.json", expected)
    save_json(root / "availability.json", statuses)
    save_json(root / "counts.json", counts)
    preview = [
        "# 颜色明细迁移样例",
        "",
        "价格0显示为空，表示未列加价；完整原值另存。",
        "",
        "| 车型 | 颜色项目 | 基础色 | 加价（元） | 价格说明 | 图库 | 分类 |",
        "|---|---|---|---:|---|---|---|",
    ]
    chosen = []
    for predicate in [
        lambda f: f["来源配置ID"] == "79396",
        lambda f: f["品牌"] == "宝马",
        lambda f: f["品牌"] == "保时捷",
        lambda f: f["备注"],
        lambda f: re.search("[A-Za-z]", f["原厂色名"]),
    ]:
        chosen.extend([f for f in expected.values() if predicate(f)][:8])
    for f in chosen:
        preview.append(
            "| "
            + " | ".join(
                str(v).replace("|", "／")
                for v in [
                    f["车型全称"],
                    f["颜色项目"],
                    "、".join(f["基础色"]),
                    f["加价（元）"] or "",
                    f["价格说明"][0],
                    "有入口" if f["图库入口"] else "未提供",
                    f["分类状态"][0],
                ]
            )
            + " |"
        )
    (root / "颜色样例.md").write_text("\n".join(preview) + "\n")
    print(json.dumps({"prepared": len(expected), **counts}, ensure_ascii=False), flush=True)
    return counts


def load_backup(root):
    data = (root / "backup.json").read_bytes()
    if hashlib.sha256(data).hexdigest() != (root / "backup.sha256").read_text().strip():
        raise ValueError("Color backup hash differs")
    return json.loads(data)


def inspect_html(body, charset, spec, side, source_id):
    text = body.decode(charset or "gb18030", errors="replace").replace('\\"', '"')
    field = "innerColorId" if side == "内饰" else "colorid"
    number = source_id.removeprefix("I")
    ok = re.search(rf'"CarSpecId"\s*:\s*{spec}(?!\d)', text) and re.search(
        rf'"{field}"\s*:\s*{number}(?!\d)', text
    )
    title = re.search(r"<title>(.*?)</title>", text, re.S | re.I)
    return bool(ok), title[1].strip() if title else ""


class GalleryRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        old = re.fullmatch(
            r"https://car\.autohome\.com\.cn/pic/spec-(\d+)-(I?\d+)\.html", request.full_url
        )
        if not old or newurl != request.full_url:
            raise ValueError("Gallery redirected outside exact source color URL")
        return super().redirect_request(request, response, code, message, headers, newurl)


def sample_links(root):
    expected = json.loads((root / "expected.json").read_text())
    path = root / "gallery-samples.json"
    results = json.loads(path.read_text()) if path.exists() else {}
    selected = []
    for predicate in [
        lambda f: f["来源配置ID"] == "79396" and f["部位"] == ["外观"],
        lambda f: f["来源配置ID"] == "79396" and f["部位"] == ["内饰"],
        lambda f: f["车系名称"] == "海鸥",
        lambda f: f["品牌"] == "宝马",
        lambda f: f["品牌"] == "保时捷",
        lambda f: f["备注"],
        lambda f: bool(re.search("[A-Za-z]", f["原厂色名"])),
    ]:
        selected.extend([f for f in expected.values() if f["图库入口"] and predicate(f)][:2])
    paused = any(r["result"] == "访问受阻" for r in results.values())
    for f in selected:
        url = f["图库入口"]
        if url in results or paused:
            continue
        assert_run_allowed(root)
        receipt = {
            "url": url,
            "time": datetime.now().astimezone().isoformat(),
            "spec": f["来源配置ID"],
            "side": f["部位"][0],
            "name": f["原厂色名"],
        }
        try:
            with urllib.request.build_opener(GalleryRedirect()).open(url, timeout=25) as response:
                body = response.read(5_000_000)
                receipt.update(
                    status=response.status,
                    final_url=response.url,
                    content_type=response.headers.get_content_type(),
                    sha256=hashlib.sha256(body).hexdigest(),
                )
                (root / "html").mkdir(exist_ok=True)
                (root / "html" / (hashlib.sha256(url.encode()).hexdigest() + ".html")).write_bytes(
                    body
                )
                text = body.decode(
                    response.headers.get_content_charset() or "gb18030", errors="replace"
                )
                if re.search(
                    r"<title>[^<]*(?:访问验证|安全验证|验证码|Access Denied)", text, re.I
                ) or re.search(r"id=[\"\'](?:geetest|captcha)", text, re.I):
                    receipt["result"] = "访问受阻"
                    paused = True
                else:
                    gallery_identity(response.url, f["来源配置ID"], f["部位"][0])
                    ok, title = inspect_html(
                        body,
                        response.headers.get_content_charset(),
                        f["来源配置ID"],
                        f["部位"][0],
                        f["源颜色编号"],
                    )
                    receipt.update(
                        title=title,
                        result="页面身份已核验（抽样）"
                        if ok and f["原厂色名"] in title and receipt["content_type"] == "text/html"
                        else "页面身份未匹配",
                    )
        except urllib.error.HTTPError as error:
            receipt.update(
                status=error.code,
                result="访问受阻" if error.code in {401, 403, 429} else "访问失败",
            )
            paused = error.code in {401, 403, 429}
        except (ValueError, OSError) as error:
            receipt.update(
                result="页面身份未匹配" if isinstance(error, ValueError) else "访问失败",
                error=str(error),
            )
        results[url] = receipt
        save_json(path, results)
        if paused:
            save_json(root / "gallery-source-paused.json", receipt)
        time.sleep(1)
    for f in expected.values():
        if f["图库入口"] in results:
            f["图库核验状态"] = [results[f["图库入口"]]["result"]]
    save_json(root / "expected.json", expected)
    print(
        json.dumps(
            {"gallery_samples": dict(Counter(r["result"] for r in results.values()))},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return results


def select(name, options, multiple=False, description=None):
    return {
        "name": name,
        "type": "select",
        "multiple": multiple,
        "options": [{"name": n} for n in options],
        **({"description": description} if description else {}),
    }


def schema(index, reverse):
    fields = [{"name": n, "type": "text"} for n in ["颜色项目", "车型全称", "model_id"]]
    fields += [
        select("部位", ["外观", "内饰"]),
        select("基础色", BASE_COLORS, True),
        select("单色／双拼", ["单色", "双拼"]),
        {
            "name": "加价（元）",
            "type": "number",
            "style": {"type": "plain", "precision": 0},
            "description": "仅源数据正数加价；空白不表示免费或标配。原始0见单项原文JSON。",
        },
        select("价格说明", ["已列加价", "未列加价"]),
        {
            "name": "图库入口",
            "type": "text",
            "style": {"type": "url"},
            "description": "源配置的对应部位、颜色图库。没有链接不等于颜色不存在。",
        },
        select(
            "图库核验状态",
            [
                "结构及身份匹配（未实测）",
                "源数据未提供",
                "页面身份已核验（抽样）",
                "访问受阻",
                "访问失败",
                "页面身份未匹配",
            ],
        ),
        select("分类状态", ["名称与色值相符", "待核对"]),
        link("对应配置", index, reverse),
    ]
    fields += [
        {"name": n, "type": "text"} for n in ["备注", "原厂色名", "完整色值", "源颜色编号", KEY]
    ]
    fields += [
        {"name": "原始顺序", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "单项原文JSON", "type": "text"},
        {"name": "来源采集时间", "type": "datetime", "style": {"format": "yyyy-MM-dd HH:mm"}},
    ]
    fields += [{"name": n, "type": "text"} for n in RIGHT]
    return fields


def capacity_error(error):
    text = json.dumps(error, ensure_ascii=False).lower()
    return bool(
        re.search(
            r"(?:行数|记录数).{0,20}(?:上限|限制|超)|recordexceedlimit|target table has reached the record limit|1254103",
            text,
        )
    )


def stage(client, root, state, name, expected):
    reverse = name + "（新入口）"
    fields = schema(state["index"], reverse)
    pending = PENDING if name == "颜色" else name + "（整理中）"
    if name not in state["tables"]:
        tables = client.call("+table-list")["tables"]
        found = [t for t in tables if t["name"] == pending]
        if not found:
            client.call(
                "+table-create",
                ["--name", pending, "--fields", json.dumps(fields, ensure_ascii=False)],
            )
            found = [t for t in client.call("+table-list")["tables"] if t["name"] == pending]
        if len(found) != 1:
            raise ValueError("Cannot identify owned color staging table")
        state["tables"][name] = found[0]["id"]
        save_json(root / "state.json", state)
    tid = state["tables"][name]
    native = client.call("+field-list", ["--table-id", tid])["fields"]
    for wanted in fields:
        found = next(f for f in native if f["name"] == wanted["name"])
        for attr in ["type", "multiple", "link_table", "bidirectional"]:
            if attr in wanted and found.get(attr) != wanted[attr]:
                raise ValueError(f"Color schema differs: {wanted['name']}/{attr}")
        for attr, value in wanted.get("style", {}).items():
            if found.get("style", {}).get(attr) != value:
                raise ValueError(f"Color style differs: {wanted['name']}/{attr}")
        if "options" in wanted and [o["name"] for o in found["options"]] != [
            o["name"] for o in wanted["options"]
        ]:
            raise ValueError(f"Color options differ: {wanted['name']}")
    if not state.get("published"):
        for view in client.call("+view-list", ["--table-id", state["index"]])["views"]:
            args = ["--table-id", state["index"], "--view-id", view["id"]]
            visible = client.call("+view-get-visible-fields", args)["visible_fields"]
            if reverse in visible:
                set_visible(client, args, [n for n in visible if n != reverse])
                time.sleep(1)
    actual = read(client, tid, KEY)
    if actual.keys() - expected.keys():
        raise ValueError("Unexpected color detail IDs in staging table")
    updates = {}
    for ident, row in actual.items():
        values = expected[ident]
        verify_fields(
            {k: values[k] for k in [KEY, "来源配置ID", "model_id", "部位", "原厂色名"]},
            row["fields"],
        )
        patch = {k: v for k, v in values.items() if not same_cell(v, row["fields"].get(k))}
        if patch:
            updates[row["record_id"]] = patch
    write_updates(client, tid, updates)
    if updates:
        print(json.dumps({"table": name, "updated": len(updates)}, ensure_ascii=False), flush=True)
    todo = [f for ident, f in expected.items() if ident not in actual]
    for start in range(0, len(todo), 200):
        assert_run_allowed(root)
        client.call(
            "+record-batch-create",
            ["--table-id", tid],
            {"create_records": todo[start : start + 200]},
        )
        print(
            json.dumps(
                {
                    "table": name,
                    "written": len(actual) + min(start + 200, len(todo)),
                    "total": len(expected),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    actual = read(client, tid, KEY) if todo or updates else actual
    verify_detail(expected, actual)
    views = client.call("+view-list", ["--table-id", tid])["views"]
    if len(views) != 1:
        raise ValueError("Color table must have exactly one default view")
    args = ["--table-id", tid, "--view-id", views[0]["id"]]
    visible = [f["name"] for f in fields if f["name"] not in HIDDEN]
    if client.call("+view-get-visible-fields", args)["visible_fields"] != visible:
        set_visible(client, args, visible)
        time.sleep(1)
    save_json(root / f"verified-{name}.json", actual)
    return actual


def verify_detail(expected, actual):
    if expected.keys() != actual.keys():
        raise ValueError("Color detail key sets differ")
    for ident, values in expected.items():
        verify_fields(values, actual[ident]["fields"])
        original = json.loads(values["单项原文JSON"])
        returned = json.loads(actual[ident]["fields"]["单项原文JSON"])
        if original != returned:
            raise ValueError(f"Color source item differs: {ident}")


def same_cell(wanted, actual):
    # Native empty select/link arrays read back as null.
    return (isinstance(wanted, list) and not wanted and actual is None) or same_value(
        wanted, actual
    )


def patch_index(client, tid, patches, allowed):
    if any(set(patch) - set(allowed) for patch in patches.values()):
        raise ValueError("Attempt to change unrelated configuration fields")
    write_updates(client, tid, patches)


def run(config, root, publish=False):
    root = assert_external(root)
    client, state = client_state(config, root)
    backup = load_backup(root)
    expected = json.loads((root / "expected.json").read_text())
    statuses = json.loads((root / "availability.json").read_text())
    # Confirm source remained intact before creating any new rows.
    source = read(client, state["source"], "来源配置ID")
    if source != backup["colors"]:
        raise ValueError("Original color table changed since backup")
    mode = state.get("mode", "single")
    verified = {}
    if mode == "single":
        try:
            verified["颜色"] = stage(client, root, state, "颜色", expected)
        except RuntimeError:
            failure = getattr(client, "last_failure", {})
            if not capacity_error(failure):
                raise
            state.update(mode="split", capacity_failure=failure)
            save_json(root / "state.json", state)
            mode = "split"
            print("Explicit row capacity limit: switching to exterior/interior tables", flush=True)
    if mode == "split":
        for name, side in [("外观颜色", "外观"), ("内饰颜色", "内饰")]:
            verified[name] = stage(
                client,
                root,
                state,
                name,
                {k: f for k, f in expected.items() if f["部位"] == [side]},
            )
    fields = client.call("+field-list", ["--table-id", state["index"]])["fields"]
    original_entry = next(f for f in backup["index_meta"]["fields"] if f["name"] == "颜色")
    current_entry = next(f for f in fields if f["id"] == original_entry["id"])
    if current_entry["name"] == OLD_ENTRY:
        state["old_renamed"] = True
        save_json(root / "state.json", state)
    elif current_entry["name"] != "颜色":
        raise ValueError("Original color entry was renamed outside this migration")
    index = read(client, state["index"], "来源配置ID")
    verify_index_unchanged(backup["index"], index, state)
    if not any(f["name"] == "颜色资料" for f in fields):
        client.call(
            "+field-create",
            ["--table-id", state["index"]],
            select(
                "颜色资料",
                AVAILABILITY,
                description="按有效颜色列表判定；没有资料不等于车辆没有颜色。",
            ),
        )
        fields = settled_fields(
            client, state["index"], lambda fs: any(f["name"] == "颜色资料" for f in fs)
        )
    allowed = ["颜色资料"]
    desired = {spec: {"颜色资料": [status]} for spec, status in statuses.items()}
    for name, rows in verified.items():
        matches = [f for f in fields if f.get("link_table") == state["tables"][name]]
        if len(matches) != 1 or matches[0]["name"] not in {name, name + "（新入口）"}:
            raise ValueError("Cannot identify exact reverse color entry")
        field_name = matches[0]["name"]
        allowed.append(field_name)
        by_spec = defaultdict(list)
        for row in rows.values():
            by_spec[row["fields"]["来源配置ID"]].append({"id": row["record_id"]})
        for spec in desired:
            actual_ids = {r["id"] for r in index[spec]["fields"].get(field_name) or []}
            wanted_ids = {r["id"] for r in by_spec[spec]}
            if actual_ids - wanted_ids:
                raise ValueError(f"Unexpected reverse color links: {spec}/{field_name}")
            desired[spec][field_name] = by_spec[spec]
    patches = {
        index[s]["record_id"]: {
            k: v for k, v in patch.items() if not same_cell(v, index[s]["fields"].get(k))
        }
        for s, patch in desired.items()
    }
    patches = {k: v for k, v in patches.items() if v}
    patch_index(client, state["index"], patches, allowed)
    index = read(client, state["index"], "来源配置ID")
    for spec, patch in desired.items():
        verify_fields(patch, index[spec]["fields"])
    verify_index_unchanged(backup["index"], index, state)
    # Recheck forward links after any reverse-link repair.
    for name, rows in verified.items():
        fresh = read(client, state["tables"][name], KEY)
        verify_detail({k: expected[k] for k in rows}, fresh)
        save_json(root / f"verified-{name}.json", fresh)
    save_json(root / "verified-index.json", index)
    if publish:
        publish_tables(client, config, root, state, backup, verified)
    report(root, expected, statuses, verified, state, len(patches))
    return {
        "rows": len(expected),
        "tables": {name: len(rows) for name, rows in verified.items()},
        "index_patches": len(patches),
        "published": bool(state.get("published")),
    }


def verify_index_unchanged(before, after, state):
    if before.keys() != after.keys():
        raise ValueError("Configuration identities changed")
    for spec, row in before.items():
        if row["record_id"] != after[spec]["record_id"]:
            raise ValueError("Configuration record ID changed")
        for name, value in row["fields"].items():
            target = OLD_ENTRY if name == "颜色" and state.get("old_renamed") else name
            if not same_value(value, after[spec]["fields"].get(target)):
                raise ValueError(f"Unrelated configuration value changed: {spec}/{name}")


def rename_index_field(client, state, root, old, new):
    fields = client.call("+field-list", ["--table-id", state["index"]])["fields"]
    field = next((f for f in fields if f["name"] == old), None)
    if field is None:
        if not any(f["name"] == new for f in fields):
            raise ValueError(f"Missing index entry: {old}")
        return
    state["index_fields"] = list(set(state.get("index_fields", [])) | {field["id"]})
    save_json(root / "state.json", state)
    body = definition(field)
    body["name"] = new
    client.call(
        "+field-update", ["--table-id", state["index"], "--field-id", field["id"], "--yes"], body
    )
    settled_fields(client, state["index"], lambda fs: any(f["name"] == new for f in fs))


def publish_tables(client, config, root, state, backup, verified):
    if not state.get("published"):
        if not state.get("old_renamed"):
            rename_index_field(client, state, root, "颜色", OLD_ENTRY)
            state["old_renamed"] = True
            save_json(root / "state.json", state)
        for name in verified:
            rename_index_field(client, state, root, name + "（新入口）", name)
        for view in backup["index_meta"]["views"]:
            args = ["--table-id", state["index"], "--view-id", view["id"]]
            original = backup["index_meta"]["detail"][view["id"]]["visible-fields"][
                "visible_fields"
            ]
            visible = []
            for name in original:
                if name == "颜色":
                    visible += ["颜色资料", *verified]
                else:
                    visible.append(name)
            visible = [n for n in visible if n not in RIGHT] + RIGHT
            if client.call("+view-get-visible-fields", args)["visible_fields"] != visible:
                set_visible(client, args, visible)
                time.sleep(1)
        blocks = client.call("+base-block-list")["blocks"]
        folder = next((b["id"] for b in blocks if b["name"] == FOLDER), None)
        if folder is None:
            client.call("+base-block-create", ["--type", "folder", "--name", FOLDER])
            folder = next(
                b["id"] for b in client.call("+base-block-list")["blocks"] if b["name"] == FOLDER
            )
        # Place active detail tables at the old color section before archiving the overview.
        original_blocks = [b for b in backup["blocks"]["blocks"] if not b.get("parent_id")]
        position = next(i for i, b in enumerate(original_blocks) if b["id"] == state["source"])
        previous = original_blocks[position - 1]["id"]
        for name in verified:
            tid = state["tables"][name]
            client.call("+base-block-rename", ["--block-id", tid, "--name", name])
            client.call("+base-block-move", ["--block-id", tid, "--after-id", previous])
            previous = tid
        client.call(
            "+base-block-rename",
            ["--block-id", state["source"], "--name", "颜色（原表备份20261019）"],
        )
        client.call("+base-block-move", ["--block-id", state["source"], "--parent-id", folder])
        if "颜色" in state["tables"] and "颜色" not in verified:
            # Capacity-limited owned staging rows remain recoverable, outside the active tables.
            tid = state["tables"]["颜色"]
            client.call(
                "+base-block-rename", ["--block-id", tid, "--name", "颜色明细（容量受限暂存）"]
            )
            client.call("+base-block-move", ["--block-id", tid, "--parent-id", folder])
            fields = client.call("+field-list", ["--table-id", state["index"]])["fields"]
            # Its unused reverse entry stays hidden along with the old entry.
            if not any(f["name"] == "颜色（新入口）" for f in fields):
                raise ValueError("Missing capacity-staging reverse field")
        current = json.loads(config.read_text())
        current["archived_color_table"] = state["source"]
        current["color_tables"] = {name: state["tables"][name] for name in verified}
        categories = {}
        for name, tid in current["category_tables"].items():
            if name == "颜色":
                categories.update(current["color_tables"])
            elif name not in current["color_tables"]:
                categories[name] = tid
        current["category_tables"] = categories
        current["color_row_key"] = KEY
        current["color_migration_run"] = root.name
        save_json(config, current)
        state["published"] = True
        save_json(root / "state.json", state)
    final = {
        "index_meta": metadata(client, state["index"]),
        "tables": {name: metadata(client, state["tables"][name]) for name in verified},
        "blocks": client.call("+base-block-list"),
    }
    save_json(root / "final-metadata.json", final)
    for name, meta in final["tables"].items():
        for view in meta["views"]:
            visible = meta["detail"][view["id"]]["visible-fields"]["visible_fields"]
            if (
                visible[:3] != ["颜色项目", "车型全称", "model_id"]
                or visible[-4:] != RIGHT
                or HIDDEN & set(visible)
            ):
                raise ValueError(f"Final visible fields differ: {name}")
    for view in backup["index_meta"]["views"]:
        before = backup["index_meta"]["detail"][view["id"]]
        after = final["index_meta"]["detail"][view["id"]]
        for setting in ["filter", "sort", "group"]:
            if before[setting] != after[setting]:
                raise ValueError(f"Unrelated index view setting changed: {setting}")
    index = read(client, state["index"], "来源配置ID")
    verify_index_unchanged(backup["index"], index, state)
    save_json(root / "verified-index.json", index)


def report(root, expected, statuses, verified, state, patches):
    counts = json.loads((root / "counts.json").read_text())
    samples = (
        json.loads((root / "gallery-samples.json").read_text())
        if (root / "gallery-samples.json").exists()
        else {}
    )
    summary = {
        "total": len(expected),
        "tables": {n: len(r) for n, r in verified.items()},
        "availability": dict(Counter(statuses.values())),
        "counts": counts,
        "sampled_links": dict(Counter(r["result"] for r in samples.values())),
        "published": bool(state.get("published")),
        "index_patches": patches,
    }
    save_json(root / "verification.json", summary)
    save_json(
        root / "colors-export.json",
        {"row_key": KEY, "items": list(expected.values()), "configuration_availability": statuses},
    )
    lines = [
        "# 颜色明细验收",
        "",
        f"时间：{datetime.now().astimezone().isoformat()}",
        "",
        f"- 源颜色项目：{len(expected)}；外观{counts['外观']}，内饰{counts['内饰']}。逐项回读一致。",
        f"- 明细表：{'；'.join(f'{n} {len(r)}行' for n, r in verified.items())}。",
        f"- 配置索引：{len(statuses)}款，配置ID及其他参数不变；颜色资料和关联集合已核对。",
        *[f"- {n}：{c}款。" for n, c in Counter(statuses.values()).items()],
        f"- 基础色待核对：{counts.get('待核对', 0)}项；原厂名称、色值完整保留。",
        f"- 有图库入口：{counts['有图库']}；源数据未提供入口：{counts['无图库']}。",
        f"- 全部入口通过URL结构及配置、部位身份检查；实际页面抽样{len(samples)}个：{summary['sampled_links']}。",
        "- 抽样通过仅代表这些链接；其余明确标注未实测。没有下载图片。",
        "- 原价格0显示为空／未列加价，不代表免费或标配；正数按整数显示。",
        "- 原表和整份原文保存在历史备份目录，未删除。",
        f"- 同名不同图库项目{sum(f['备注'] == '同名不同图库项目' for f in expected.values())}项、同名不同色值项目{sum(f['备注'] == '同名不同色值项目' for f in expected.values())}项，均分别保留。",
        *(
            ["- 单表写至20000行后源服务明确返回容量上限，已按批准方案拆分两表；受限暂存表已归档。"]
            if state.get("mode") == "split"
            else []
        ),
        "",
        "## 使用",
        "",
        "从配置索引打开颜色关联，或在颜色明细按车型、部位、基础色筛选。点击图库入口查看对应颜色。",
        "追溯字段默认隐藏；品牌、车系名称、配置名称、来源配置ID位于最右侧。",
        "",
        "## 链接抽样",
        "",
        "| 车型配置ID | 部位 | 色名 | 结果 |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {r['spec']} | {r['side']} | {r['name']} | {r['result']} |" for r in samples.values()
    ]
    (root / "颜色明细验收.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description="配置颜色明细：备份、预览、迁移、全量回读")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--action", choices=["prepare", "sample", "stage", "publish"], required=True
    )
    args = parser.parse_args()
    root = assert_external(args.root)
    root.mkdir(parents=True, exist_ok=True)
    if args.action == "prepare":
        prepare(args.config, root)
    elif args.action == "sample":
        sample_links(root)
    else:
        print(json.dumps(run(args.config, root, args.action == "publish"), ensure_ascii=False))


if __name__ == "__main__":
    main()
