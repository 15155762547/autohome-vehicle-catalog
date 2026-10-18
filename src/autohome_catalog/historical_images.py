"""Replace cover-only models with dated, same-power official or authorized general-gallery URLs."""

import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, build_opener

from .boolean_migration import verify_fields, write_updates
from .feishu import Client, record_index
from .human_layout import read_records
from .image_urls import all_url, any_data, any_photos
from .images import gallery_url, now, page_data, photos
from .storage import assert_external, assert_run_allowed, guard_response, save_json


def menu(data, latest_year):
    """Prefer the accepted latest year, then older years; never use unaccepted newer years."""
    return sorted(
        [
            {**row, "year": int(bucket["year"])}
            for bucket in data.get("specList", []) + data.get("specOtherList", [])
            if int(bucket["year"]) <= latest_year
            for row in bucket.get("list", [])
            if int(row.get("count") or 0) > 0
        ],
        key=lambda row: (-row["year"], -int(row["count"]), int(row["specid"])),
    )


class PowerUnavailable(ValueError):
    pass


def matching_power(payload, target, spec, year):
    if payload.get("returncode") != 0:
        raise ValueError("Unsuccessful parameter response")
    result = payload["result"]
    if int(result["bread"]["seriesid"]) != target["series_id"]:
        raise ValueError("Historical parameter series mismatch")
    rows = [row for row in result["datalist"] if int(row["specid"]) == spec]
    if len(rows) != 1 or not rows[0]["specname"].startswith(f"{year}款"):
        raise ValueError("Historical parameter configuration/year mismatch")
    titles = [
        item["titleid"]
        for group in result["titlelist"]
        for item in group["items"]
        if item["itemname"] == "能源类型"
    ]
    energies = {
        item["itemname"]
        for item in rows[0]["paramconflist"]
        if item["titleid"] in titles and item["itemname"] not in (None, "", "-", "—")
    }
    if not energies:
        raise PowerUnavailable("历史配置未返回能源类型，不能确认动力")
    if len(energies) != 1:
        raise ValueError("Historical power types conflict")
    return energies == {target["power"]}


def image_patch(result):
    images, target = result["images"], result["target"]
    year = result["image_year"]
    if not images or len(images) > 15 or len({p["url"] for p in images}) != len(images):
        raise ValueError("Missing, duplicate or excessive historical images")
    general = result.get("gallery_mode") == "general"
    kind = "普通图库图片" if general else "官图"
    scope = (
        f"当前车型最新年款为{target['year']}款；本次使用{year}款{target['power']}历史{kind}，"
        "不代表最新年款外观或配置。历史车型可能已停售，仅用作车型展示参考。"
        if year < target["year"]
        else f"本次使用当前{year}款{target['power']}{kind}，不涉及历史年款替代。"
    )
    return {
        "图片年款": str(year),
        "图片URL清单": json.dumps([p["url"] for p in images], ensure_ascii=False),
        "图片数": len(images),
        "图片类别": "、".join(dict.fromkeys(p.get("category", "官图") for p in images)),
        "图片验证方式": "源页面HTTP200；核对配置、年款及动力；URL未直连验证",
        "图片说明": (
            scope + f"本次提取{len(images)}张，最多15张；同批图片未混用不同年款或动力。"
            f"来源配置ID：{','.join(str(s) for s in sorted({p['spec_id'] for p in images}))}。"
            "只保存原始URL，未下载图片文件。"
            + ("本次经授权改用普通图库，包含外观、内饰等类别，并非官图专属。" if general else "")
        ),
        "图片采集来源页": "\n".join(dict.fromkeys(p["source_page"] for p in images)),
        "图片检查时间": result["checked_at"],
    }


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GalleryUnavailable(ValueError):
    pass


def gallery_redirect(status, location, url):
    # A missing series gallery returns to the generic gallery landing page.
    # Do not follow it or confuse its unrelated pictures with this model's pictures.
    return status == 307 and location == "/cars/imglist" and "/cars/imglist-x-x-" in url


class Fetcher:
    def __init__(self, root):
        self.root = root
        self.opener = build_opener(NoRedirect)
        self.last = 0

    def get(self, url):
        assert_run_allowed(self.root)
        key = hashlib.sha256(url.encode()).hexdigest()
        receipt_path = self.root / "sources" / f"{key}.json"
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            raw = (self.root / receipt["file"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
                raise ValueError("Source cache hash mismatch")
            return raw.decode("utf-8")
        time.sleep(max(0, 0.8 - (time.monotonic() - self.last)))
        try:
            response = self.opener.open(url, timeout=30)
        except HTTPError as exc:
            response = exc
        self.last = time.monotonic()
        with response:
            raw = response.read()
            status = response.status
            content_type = response.headers.get("Content-Type", "")
            location = response.headers.get("Location", "")
        digest = hashlib.sha256(raw).hexdigest()
        raw_path = self.root / "sources" / f"{digest}.raw"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(raw)
        receipt = dict(
            url=url,
            status=status,
            sha256=digest,
            file=str(raw_path.relative_to(self.root)),
            checked_at=now(),
            location=location,
        )
        if gallery_redirect(status, location, url):
            save_json(self.root / "problems" / f"{key}.json", receipt)
            raise GalleryUnavailable("该车型图库入口跳转通用图库页，未跟随，未采入其他车型图片")
        try:
            guard_response(status, raw, content_type)
        except ValueError as exc:
            save_json(self.root / "problems" / f"{key}.json", receipt)
            if "paused" in str(exc).lower():
                save_json(self.root / "PAUSED.json", {"reason": str(exc), "source": receipt})
            raise
        save_json(receipt_path, receipt)
        return raw.decode("utf-8")


def collect(fetcher, target):
    data = page_data(fetcher.get(gallery_url(target["series_id"])), target["series_id"])
    selected_year, images, ids, urls = None, [], set(), set()
    problems = []
    for candidate in menu(data, target["year"]):
        year, spec = candidate["year"], int(candidate["specid"])
        if selected_year is not None and year != selected_year:
            break
        endpoint = "https://www.autohome.com.cn/web-main/car/param/getParamConf?"
        payload = json.loads(
            fetcher.get(endpoint + urlencode({"mode": 1, "site": 2, "specid": spec}))
        )
        try:
            if not matching_power(payload, target, spec, year):
                continue
        except PowerUnavailable as exc:
            problems.append({"spec_id": spec, "year": year, "reason": str(exc)})
            continue
        for page in range(1, 3):
            url = gallery_url(target["series_id"], spec, page)
            detail = page_data(fetcher.get(url), target["series_id"], spec)
            rows, total = photos(detail, {**target, "year": year}, spec, url)
            for photo in rows:
                if photo["picture_id"] in ids or photo["url"] in urls:
                    continue
                ids.add(photo["picture_id"])
                urls.add(photo["url"])
                images.append(photo)
                selected_year = year
                if len(images) == 15:
                    break
            if len(images) == 15 or not rows or total <= page * len(rows):
                break
        if len(images) == 15:
            break
    return dict(
        target=target,
        image_year=selected_year,
        images=images,
        checked_at=now(),
        status="已补历史官图" if images else "动力待核实" if problems else "无匹配历史官图",
        problems=problems,
    )


def collect_general(fetcher, target):
    data = any_data(fetcher.get(all_url(target["series_id"])), target)
    selected_year, images, ids, urls = None, [], set(), set()
    problems = []
    for candidate in menu(data, target["year"]):
        year, spec = candidate["year"], int(candidate["specid"])
        if selected_year is not None and year != selected_year:
            break
        endpoint = "https://www.autohome.com.cn/web-main/car/param/getParamConf?"
        payload = json.loads(
            fetcher.get(endpoint + urlencode({"mode": 1, "site": 2, "specid": spec}))
        )
        try:
            if not matching_power(payload, target, spec, year):
                continue
        except PowerUnavailable as exc:
            problems.append({"spec_id": spec, "year": year, "reason": str(exc)})
            continue
        for page in range(1, 3):
            url = all_url(target["series_id"], spec).replace("-1.html", f"-{page}.html")
            detail = any_data(fetcher.get(url), {**target, "year": year}, spec)
            rows = any_photos(detail, target, spec, url)
            added = 0
            for photo in rows:
                if photo["picture_id"] in ids or photo["url"] in urls:
                    continue
                ids.add(photo["picture_id"])
                urls.add(photo["url"])
                images.append(photo)
                selected_year = year
                added += 1
                if len(images) == 15:
                    break
            if len(images) == 15 or not added:
                break
        if len(images) == 15:
            break
    return dict(
        target=target,
        image_year=selected_year,
        images=images,
        checked_at=now(),
        gallery_mode="general",
        problems=problems,
        status="已补普通图库" if images else "动力待核实" if problems else "无匹配普通图库",
    )


def run(config, root, prior, apply=False, general_history=None):
    root = assert_external(root)
    root.mkdir(parents=True, exist_ok=True)
    client = Client(config)
    client.config["_image_operation"] = True
    tid = client.config["tables"]["models"]
    before_path = root / "models-before.json"
    live = record_index(read_records(client, tid, "model_id"), "model_id")
    if not before_path.exists():
        save_json(before_path, live)
    before = json.loads(before_path.read_text())
    previous = (
        json.loads((root / "models-after.json").read_text())
        if (root / "models-after.json").exists()
        else {}
    )
    targets = {
        mid: json.loads((prior / "groups" / f"{mid}.json").read_text())["target"]
        for mid, row in before.items()
        if row["fields"].get("图片数") == 1 and row["fields"].get("图片类别") == "车系封面"
    }
    if general_history:
        general_history = assert_external(general_history)
        allowed = {
            p.stem
            for p in (general_history / "groups").glob("*.json")
            if json.loads(p.read_text())["status"] == "图库入口不可用"
        }
        targets = {mid: target for mid, target in targets.items() if mid in allowed}
    save_json(root / "targets.json", list(targets.values()))
    fetcher = Fetcher(root)
    for index, (mid, target) in enumerate(targets.items(), 1):
        path = root / "groups" / f"{mid}.json"
        if path.exists():
            continue
        try:
            result = (
                collect_general(fetcher, target) if general_history else collect(fetcher, target)
            )
        except GalleryUnavailable as exc:
            result = dict(
                target=target, images=[], status="图库入口不可用", reason=str(exc), checked_at=now()
            )
        except Exception as exc:
            if (root / "PAUSED.json").exists():
                print(f"PAUSED {mid}: {exc}", flush=True)
                break
            result = dict(
                target=target, images=[], status="失败", reason=str(exc), checked_at=now()
            )
        save_json(path, result)
        print(
            f"{index}/{len(targets)} {mid} {result['status']} {len(result['images'])}", flush=True
        )
    results = [json.loads(path.read_text()) for path in sorted((root / "groups").glob("*.json"))]
    schema = client.call("+field-list", ["--table-id", tid])["fields"]
    names = {field["name"] for field in schema}
    patches, expected = {}, {}
    for result in results:
        mid = result["target"]["model_id"]
        if mid not in targets:
            raise ValueError("Unexpected historical result")
        if result["images"]:
            patch = {k: v for k, v in image_patch(result).items() if k in names}
            if not {"图片年款", "图片URL清单", "图片数", "图片说明"} <= patch.keys():
                raise ValueError("Required image fields were removed")
        else:
            source_kind = "普通图库" if general_history else "历史官图"
            patch = {
                "图片说明": before[mid]["fields"].get("图片说明", "")
                + f"；{result['checked_at']}复查{source_kind}：{result['status']}。"
                + result.get("reason", "")
            }
        if live[mid]["record_id"] != before[mid]["record_id"]:
            raise ValueError("Model record identity changed")
        for name in patch:
            if live[mid]["fields"].get(name) not in (
                before[mid]["fields"].get(name),
                patch[name],
                previous.get(mid, {}).get("fields", {}).get(name),
            ):
                raise ValueError(f"Image field edited concurrently: {mid}/{name}")
        expected[mid] = patch
        if any(live[mid]["fields"].get(k) != v for k, v in patch.items()):
            patches[live[mid]["record_id"]] = patch
    save_json(root / "write-plan.json", patches)
    if apply and patches:
        write_updates(client, tid, patches)
    actual = record_index(read_records(client, tid, "model_id"), "model_id") if apply else live
    if apply:
        if actual.keys() != live.keys():
            raise ValueError("Model key set changed")
        for mid, row in actual.items():
            if row["record_id"] != live[mid]["record_id"]:
                raise ValueError("Model record ID changed")
            verify_fields(
                {k: v for k, v in live[mid]["fields"].items() if k not in expected.get(mid, {})},
                row["fields"],
            )
            if mid in expected:
                # URL text may be returned as Markdown; compare parsed lists separately.
                from .image_sync import url_lines

                for key, value in expected[mid].items():
                    if key in {"图片URL清单", "图片采集来源页"}:
                        if url_lines(value) != url_lines(row["fields"].get(key)):
                            raise ValueError("Historical URLs differ on read-back")
                    else:
                        verify_fields({key: value}, row["fields"])
        save_json(root / "models-after.json", actual)
        for result in results:
            if result["images"]:
                save_json(
                    prior / "historical-overrides" / f"{result['target']['model_id']}.json", result
                )
    summary = dict(
        targets=len(targets),
        processed=len(results),
        full15=sum(len(r["images"]) == 15 for r in results),
        under15=sum(0 < len(r["images"]) < 15 for r in results),
        no_match=sum(r["status"] in {"无匹配历史官图", "无匹配普通图库"} for r in results),
        failed=sum(r["status"] == "失败" for r in results),
        gallery_unavailable=sum(r["status"] == "图库入口不可用" for r in results),
        power_unverified=sum(r["status"] == "动力待核实" for r in results),
        remaining=len(targets) - len(results),
        updated=len(patches) if apply else 0,
        verified=apply,
        deleted_status_field_restored=False,
        paused=(root / "PAUSED.json").exists(),
        checked_at=now(),
    )
    save_json(root / "verification.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--general-history", type=Path, help="授权普通图库补采，仅选该运行中官图入口不可用的组"
    )
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.config, args.run, args.prior, args.apply, args.general_history),
            ensure_ascii=False,
        )
    )
