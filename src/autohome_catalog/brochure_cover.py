"""Download Autohome listing covers and attach one representative image per model."""

import argparse
import hashlib
import json
import re
import shutil
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .base_utils import read_records, set_visible, settled_fields
from .feishu import Client, record_index, same_value
from .image_sync import url_lines
from .storage import assert_external, assert_run_allowed, save_json

FIELD = "brochure_cover"
HOSTS = {"car2.autoimg.cn", "car3.autoimg.cn"}


def guard(config, command, args):
    tid = args[args.index("--table-id") + 1] if "--table-id" in args else None
    if command in {"+field-list", "+record-list", "+view-list", "+view-get-visible-fields"}:
        return
    if command == "+record-upload-attachment":
        state = config.get("_brochure_cover", {})
        fid = args[args.index("--field-id") + 1] if "--field-id" in args else None
        if (
            tid == config["tables"]["models"]
            and isinstance(state, dict)
            and fid == state.get("field_id")
            and fid
        ):
            return
        raise ValueError("Only the resolved brochure_cover field may receive attachments")
    if tid == config["tables"]["models"] and command in {
        "+field-create",
        "+record-download-attachment",
        "+view-set-visible-fields",
    }:
        return
    raise ValueError(f"Outside model cover operation: {command}")


def targets(rows):
    result = {}
    for key, row in record_index(rows, "model_id").items():
        urls = url_lines(row["fields"].get("车系图片URL"))
        if len(urls) != 1 or urlsplit(urls[0]).hostname not in HOSTS:
            raise ValueError(f"Missing or unapproved listing cover: {key}")
        result[key] = {
            "model_id": key,
            "record_id": row["record_id"],
            "url": urls[0],
            "name": row["fields"]["车系名称"],
            "series_id": row["fields"]["来源车系ID"],
        }
    return result


def image_extension(body, content_type):
    if body.startswith(b"\x89PNG\r\n\x1a\n") and content_type == "image/png":
        return ".png"
    if body.startswith(b"\xff\xd8\xff") and content_type in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP" and content_type == "image/webp":
        return ".webp"
    raise ValueError("Cover response is not a supported image")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        raise ValueError("Unexpected cover redirect; no alternate identity or URL used")


def download(root, cache, url, attempt=0):
    assert_run_allowed(root)
    if (root / "source-paused.json").exists():
        raise ValueError("Cover source paused; inspect source-paused.json")
    if urlsplit(url).scheme != "https" or urlsplit(url).hostname not in HOSTS:
        raise ValueError("Unapproved cover source URL")
    ident = hashlib.sha256(url.encode()).hexdigest()
    receipt_path = root / "downloads" / f"{ident}.json"
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        path = Path(receipt["file"])
        if (
            receipt["url"] != url
            or hashlib.sha256(path.read_bytes()).hexdigest() != receipt["sha256"]
        ):
            raise ValueError("Cover cache identity/hash differs")
        return receipt
    try:
        with urllib.request.build_opener(NoRedirect()).open(url, timeout=25) as response:
            body = response.read(10 * 1024 * 1024 + 1)
            if len(body) > 10 * 1024 * 1024:
                raise ValueError("Listing cover exceeds 10 MiB")
            content_type = response.headers.get_content_type()
            if not content_type.startswith("image/") and re.search(
                r"安全验证|访问验证|访问受限|captcha|Access Denied",
                body[:100000].decode("utf-8", errors="replace"),
                re.I,
            ):
                save_json(root / "source-paused.json", {"url": url, "reason": "verification page"})
                raise ValueError("Cover source paused: verification page")
            extension = image_extension(body, content_type)
            path = cache / (ident + extension)
            path.write_bytes(body)
            receipt = {
                "url": url,
                "file": str(path),
                "sha256": hashlib.sha256(body).hexdigest(),
                "size": len(body),
                "mime": content_type,
                "extension": extension,
                "http_status": response.status,
                "time": datetime.now().astimezone().isoformat(),
            }
            save_json(receipt_path, receipt)
            return receipt
    except urllib.error.HTTPError as error:
        if error.code in {401, 403, 429}:
            save_json(root / "source-paused.json", {"url": url, "status": error.code})
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        if attempt >= 2:
            raise
        time.sleep(1 + attempt)
        return download(root, cache, url, attempt + 1)


def attachment_matches(items, target, receipt):
    if len(items or []) != 1:
        return False
    expected_name = target["model_id"] + "-" + receipt["sha256"][:12] + receipt["extension"]
    return (
        items[0].get("name") == expected_name
        and items[0].get("size") == receipt["size"]
        and bool(items[0].get("file_token"))
    )


def unchanged(before, after):
    if before.keys() != after.keys():
        raise ValueError("Model key set changed")
    for key, row in before.items():
        if row["record_id"] != after[key]["record_id"]:
            raise ValueError("Model record identity changed")
        for name, value in row["fields"].items():
            if name != FIELD and not same_value(value, after[key]["fields"].get(name)):
                raise ValueError(f"Unrelated model field changed: {key}/{name}")


def run(config, root, model_ids=(), roundtrip=False):
    root = assert_external(root)
    root.mkdir(parents=True, exist_ok=True)
    client = Client(config)
    client.config["_brochure_cover"] = True
    tid = client.config["tables"]["models"]
    cache = assert_external(config.parent / (root.name + "-files"))
    cache.mkdir(exist_ok=True)
    backup = root / "models-before.json"
    if not backup.exists():
        save_json(backup, read_records(client, tid, "model_id"))
        save_json(root / "fields-before.json", client.call("+field-list", ["--table-id", tid]))
        save_json(root / "views-before.json", client.call("+view-list", ["--table-id", tid]))
    before = record_index(json.loads(backup.read_text()), "model_id")
    wanted = targets(list(before.values()))
    if len(wanted) != 1010:
        raise ValueError("Expected the accepted 1010-model scope")
    selected = {k: wanted[k] for k in model_ids} if model_ids else wanted
    urls = list(dict.fromkeys(t["url"] for t in selected.values()))
    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = dict(
            zip(urls, executor.map(lambda url: download(root, cache, url), urls), strict=True)
        )
    current = record_index(read_records(client, tid, "model_id"), "model_id")
    unchanged(before, current)
    fields = client.call("+field-list", ["--table-id", tid])["fields"]
    if not any(f["name"] == FIELD for f in fields):
        client.call(
            "+field-create",
            ["--table-id", tid],
            {
                "name": FIELD,
                "type": "attachment",
                "description": "每个model_id一张车型代表图，来自汽车之家车系列表封面，用于画廊或应用封面；不作为具体配置或年款细节的证据。",
            },
        )
        fields = settled_fields(client, tid, lambda fs: any(f["name"] == FIELD for f in fs))
    field = next(f for f in fields if f["name"] == FIELD)
    if field["type"] != "attachment":
        raise ValueError("Existing brochure_cover is not an attachment field")
    client.config["_brochure_cover"] = {"field_id": field["id"]}
    uploaded = skipped = 0
    for key, target in selected.items():
        assert_run_allowed(root)
        receipt = receipts[target["url"]]
        items = current[key]["fields"].get(FIELD)
        if attachment_matches(items, target, receipt):
            skipped += 1
            continue
        if items:
            raise ValueError(f"Existing cover differs; never append or replace silently: {key}")
        name = key + "-" + receipt["sha256"][:12] + receipt["extension"]
        local = cache / name
        if not local.exists():
            shutil.copyfile(receipt["file"], local)
        if hashlib.sha256(local.read_bytes()).hexdigest() != receipt["sha256"]:
            raise ValueError("Cover file hash changed before upload")
        client.call(
            "+record-upload-attachment",
            [
                "--table-id",
                tid,
                "--record-id",
                target["record_id"],
                "--field-id",
                field["id"],
                "--file",
                str(local.relative_to(config.parent)),
            ],
        )
        uploaded += 1
        save_json(
            root / "progress.json",
            {
                "last_model_id": key,
                "uploaded_this_run": uploaded,
                "skipped_this_run": skipped,
                "selected": len(selected),
            },
        )
        print(
            json.dumps(
                {"cover": key, "uploaded": uploaded, "skipped": skipped, "selected": len(selected)},
                ensure_ascii=False,
            ),
            flush=True,
        )
    after = record_index(read_records(client, tid, "model_id"), "model_id")
    unchanged(before, after)
    for key, target in selected.items():
        if not attachment_matches(after[key]["fields"].get(FIELD), target, receipts[target["url"]]):
            raise ValueError(f"Cover count/name/size/token differs on read-back: {key}")
    save_json(root / "models-after.json", list(after.values()))
    roundtrips = []
    if roundtrip:
        checks = cache / "readback"
        checks.mkdir(exist_ok=True)
        for key in list(selected)[:3]:
            item = after[key]["fields"][FIELD][0]
            out = checks / item["name"]
            client.call(
                "+record-download-attachment",
                [
                    "--table-id",
                    tid,
                    "--record-id",
                    after[key]["record_id"],
                    "--file-token",
                    item["file_token"],
                    "--output",
                    str(out.relative_to(config.parent)),
                    "--overwrite",
                ],
            )
            if (
                hashlib.sha256(out.read_bytes()).hexdigest()
                != receipts[selected[key]["url"]]["sha256"]
            ):
                raise ValueError("Feishu attachment round-trip hash differs")
            roundtrips.append(key)
    for view in client.call("+view-list", ["--table-id", tid])["views"]:
        args = ["--table-id", tid, "--view-id", view["id"]]
        visible = client.call("+view-get-visible-fields", args)["visible_fields"]
        if not (root / "visible-before.json").exists():
            save_json(
                root / "visible-before.json", {"visible_fields": [n for n in visible if n != FIELD]}
            )
        base = [n for n in visible if n != FIELD]
        position = base.index("车系名称") + 1
        base.insert(position, FIELD)
        if visible != base:
            set_visible(client, args, base)
    result = {
        "models": len(after),
        "selected": len(selected),
        "uploaded": uploaded,
        "skipped": skipped,
        "with_one_cover": sum(len(r["fields"].get(FIELD) or []) == 1 for r in after.values()),
        "roundtrip_hash_verified": roundtrips,
        "unrelated_values_unchanged": True,
    }
    save_json(root / ("sample-verification.json" if model_ids else "verification.json"), result)
    if not model_ids:
        current_config = json.loads(config.read_text())
        current_config["models_cover_field"] = FIELD
        save_json(config, current_config)
        (root / "车型封面验收.md").write_text(
            "# 车型代表图验收\n\n"
            f"- 车型1010行；每行brochure_cover附件恰好1张，全部回读核对名称、大小及file_token。\n"
            f"- 使用汽车之家列表封面，复用{len(urls)}个源URL；下载检查HTTP状态、图片类型、文件签名与SHA256。\n"
            "- 原图片URL清单、图片数、车型身份、配置关联及其他原值均保持不变。\n"
            "- 封面字段位于车系名称之后；未增加表或视图。\n"
            "- 封面用于车型展示，不作为具体年款、动力配置细节的图片证据。\n"
            f"- 飞书附件下载回验哈希一致：{len(roundtrips)}张。\n"
        )
    return result


def main():
    parser = argparse.ArgumentParser(description="每个model_id保存一张brochure_cover图片附件")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model-id", action="append", default=[])
    parser.add_argument("--roundtrip", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(run(args.config, args.root, args.model_id, args.roundtrip), ensure_ascii=False)
    )


if __name__ == "__main__":
    main()
