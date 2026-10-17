"""Latest-year official gallery URLs; media requests are HEAD only."""

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import scrapy
from pydantic import BaseModel, Field
from scrapy.utils.defer import maybe_deferred_to_future

from .storage import access_pause_path, assert_external, save_json


def now():
    return datetime.now(timezone.utc).isoformat()


class Target(BaseModel):
    model_id: str
    series_id: int
    name: str
    brand: str
    power: str
    year: int
    model_record: str
    specs: list[int] = Field(min_length=1)


def fingerprint(target):
    return hashlib.sha256(json.dumps(target, sort_keys=True).encode()).hexdigest()


def prepare(source, root):
    source, root = assert_external(source), assert_external(root)
    raw = source.read_bytes()
    groups = {}
    for row in json.loads(raw):
        f = row["fields"]
        key = f["model_id"]
        year = int(re.match(r"(\d{4})款", f["配置名称"])[1])
        entry = groups.setdefault(
            key,
            dict(
                model_id=key,
                series_id=int(f["来源车系ID"]),
                name=f["车系名称"],
                brand=f["品牌"],
                power=f["能源类型"],
                year=year,
                model_record=f["所属车系"][0]["id"],
                specs=[],
            ),
        )
        if year > entry["year"]:
            entry.update(year=year, specs=[])
        if year == entry["year"]:
            entry["specs"].append(int(f["来源配置ID"]))
    targets = [
        Target(**{**v, "specs": sorted(set(v["specs"]))}).model_dump()
        for _, v in sorted(groups.items())
    ]
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source": str(source),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "targets": targets,
        "created_at": now(),
    }
    path = root / "targets.json"
    if path.exists() and json.loads(path.read_text())["targets"] != targets:
        raise ValueError("Existing image scope differs")
    save_json(path, manifest)
    return {"groups": len(targets), "candidates": sum(len(t["specs"]) for t in targets)}


def gallery_url(series, spec="x", page=1):
    return f"https://www.autohome.com.cn/cars/imglist-x-x-{series}-{spec}-53-x-x-x-x-{page}.html"


def image_url(url):
    if url.startswith("//"):
        url = "https:" + url
    url = re.sub(r"^http:", "https:", url)
    p = urlsplit(url)
    if (
        p.scheme != "https"
        or not re.fullmatch(r"car\d*\.autoimg\.cn", p.hostname or "")
        or p.username
        or p.password
        or p.port
        or not p.path
    ):
        raise ValueError("Unexpected image host or URL")
    return url


def page_data(text, series, spec=None):
    match = re.search(r'<script\b[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', text, re.S)
    if not match:
        raise ValueError("Missing gallery data")
    data = json.loads(match[1])["props"]["pageProps"]
    bread, params = data["breadInfo"], data["params"]
    if int(bread.get("seriesid") or 0) != series or int(params.get("typeId") or 0) != 53:
        raise ValueError("Gallery series/category identity mismatch")
    if spec is not None and (
        int(bread.get("specid") or 0) != spec or int(params.get("specId") or 0) != spec
    ):
        raise ValueError("Gallery specification identity mismatch")
    return data


def candidates(data, target):
    counts = {}
    for bucket in data.get("specList", []) + data.get("specOtherList", []):
        for row in bucket.get("list", []):
            sid = int(row["specid"])
            if sid in target["specs"]:
                if int(bucket["year"]) != target["year"]:
                    raise ValueError("Gallery year differs from accepted configuration")
                counts[sid] = int(row.get("count") or 0)
    # Missing menu entries are explicitly checked, never declared image-free from a cache.
    return sorted(target["specs"], key=lambda x: (-counts.get(x, -1), x))


def photos(data, target, spec, source_page):
    if not re.match(str(target["year"]) + r"款\b", data["breadInfo"].get("specname", "")):
        raise ValueError("Official gallery is not the accepted latest year")
    result, total = [], 0
    for group in data.get("SpecPicList", {}).get("speclist", []):
        if int(group["specid"]) != spec:
            raise ValueError("Unrelated photo group")
        total = int(group.get("total") or 0)
        for p in group.get("piclist", []):
            if int(p.get("specid") or 0) != spec or int(p.get("claid") or 0) != 53:
                raise ValueError("Photo belongs to a different configuration/category")
            if int(p.get("mediatype") or 0) != 0:
                continue
            result.append(
                dict(
                    picture_id=int(p["picid"]),
                    spec_id=spec,
                    url=image_url(p["picpath"]),
                    source_page=source_page,
                )
            )
    return result, total


class SourcePaused(Exception):
    pass


class ImageRequestGuard:
    """Block queued requests immediately once their source is paused."""

    def process_request(self, request, spider):
        host = urlsplit(request.url).hostname
        if host in spider.paused or (spider.root / "STOP").exists():
            raise SourcePaused(f"Source paused before request: {host}")
        if urlsplit(request.url).path == "/robots.txt":
            if host == "www.autohome.com.cn" or re.fullmatch(r"car\d*\.autoimg\.cn", host or ""):
                return
        if request.method == "HEAD":
            image_url(request.url)
        elif host != "www.autohome.com.cn" or not re.fullmatch(
            r"/cars/imglist-x-x-\d+-(?:x|\d+)-53-x-x-x-x-\d+\.html",
            urlsplit(request.url).path,
        ):
            raise ValueError("Image task permits only official gallery HTML and image HEAD")

    def process_response(self, request, response, spider):
        spider.check(response.replace(request=request))
        return response


class OfficialImagesSpider(scrapy.Spider):
    name = "official_image_urls"

    def __init__(self, root, model_ids=None, **kwargs):
        super().__init__(**kwargs)
        self.root = assert_external(Path(root))
        self.targets = json.loads((self.root / "targets.json").read_text())["targets"]
        self.selected = set(model_ids or [])
        if self.selected - {t["model_id"] for t in self.targets}:
            raise ValueError("Unknown model_id")
        self.paused = set()
        self.results = self.root / "groups"
        self.results.mkdir(exist_ok=True)
        self.sources = self.root / "sources"
        self.sources.mkdir(exist_ok=True)
        for marker in (self.root / "pauses").glob("*.json"):
            self.paused.add(json.loads(marker.read_text())["host"])

        shared = access_pause_path().parent / "image-source-pauses"
        for marker in shared.glob("*.json"):
            self.paused.add(json.loads(marker.read_text())["host"])

    async def start(self):
        for target in self.targets:
            if self.selected and target["model_id"] not in self.selected:
                continue
            path = self.results / (target["model_id"] + ".json")
            if path.exists():
                prior = json.loads(path.read_text())
                if prior["target_sha256"] != fingerprint(target):
                    raise ValueError("Resume target changed")
                if prior["status"] in {"满15张", "不足15张", "无匹配官图"}:
                    continue
            yield scrapy.Request(
                gallery_url(target["series_id"]),
                callback=self.collect,
                cb_kwargs={"target": target},
                dont_filter=True,
                meta={"handle_httpstatus_all": True},
                errback=self.initial_error,
            )

    def initial_error(self, failure):
        target = failure.request.cb_kwargs["target"]
        self.finish(
            target,
            [],
            [{"reason": str(failure.value)}],
            "访问受阻" if isinstance(failure.value, SourcePaused) else "失败",
        )

    def check(self, response):
        host = urlsplit(response.url).hostname
        mime = response.headers.get(b"Content-Type", b"").decode().lower()
        challenge = (
            response.request.method != "HEAD"
            and hasattr(response, "text")
            and any(x in response.text.lower() for x in ("访问验证", "安全验证", "captcha"))
        )
        if response.status in {401, 403, 429} or challenge:
            self.paused.add(host)
            save_json(
                self.root / "pauses" / (host + ".json"),
                {"host": host, "url": response.url, "status": response.status, "time": now()},
            )
            save_json(
                access_pause_path().parent / "image-source-pauses" / (host + ".json"),
                {"host": host, "url": response.url, "status": response.status, "time": now()},
            )
            raise SourcePaused(f"{host}: HTTP {response.status}/verification")
        return mime

    async def fetch(self, url, method="GET"):
        host = urlsplit(url).hostname
        if host in self.paused:
            raise SourcePaused(f"{host}: paused")
        if (self.root / "STOP").exists():
            raise SourcePaused("STOP requested")
        if method == "HEAD":
            image_url(url)
        elif host != "www.autohome.com.cn" or not re.fullmatch(
            r"/cars/imglist-x-x-\d+-(?:x|\d+)-53-x-x-x-x-\d+\.html", urlsplit(url).path
        ):
            raise ValueError("Only official gallery HTML may be fetched")
        response = await maybe_deferred_to_future(
            self.crawler.engine.download(
                scrapy.Request(
                    url, method=method, dont_filter=True, meta={"handle_httpstatus_all": True}
                )
            )
        )
        self.check(response)
        if method == "GET":
            digest = hashlib.sha256(response.body).hexdigest()
            path = self.sources / (digest + ".html")
            if not path.exists():
                path.write_bytes(response.body)
            save_json(
                self.sources / (hashlib.sha256(url.encode()).hexdigest() + ".json"),
                {"url": url, "sha256": digest, "status": response.status, "checked_at": now()},
            )
        return response

    async def collect(self, response, target):
        good, problems = [], []
        try:
            self.check(response)
            if response.status != 200:
                raise ValueError(f"Inventory HTTP {response.status}")
            digest = hashlib.sha256(response.body).hexdigest()
            (self.sources / (digest + ".html")).write_bytes(response.body)
            save_json(
                self.sources / (hashlib.sha256(response.url.encode()).hexdigest() + ".json"),
                {
                    "url": response.url,
                    "sha256": digest,
                    "checked_at": now(),
                    "status": response.status,
                },
            )
            inventory = page_data(response.text, target["series_id"])
            seen_ids, seen_urls = set(), set()
            for spec in candidates(inventory, target):
                page, visited = 1, set()
                while len(good) < 15:
                    url = gallery_url(target["series_id"], spec, page)
                    res = await self.fetch(url)
                    if res.status != 200:
                        raise ValueError(f"Gallery HTTP {res.status}: {url}")
                    data = page_data(res.text, target["series_id"], spec)
                    rows, total = photos(data, target, spec, url)
                    fresh = [p for p in rows if p["picture_id"] not in visited]
                    if total and not fresh:
                        raise ValueError("Empty or repeated gallery page before total")
                    for photo in fresh:
                        visited.add(photo["picture_id"])
                        if photo["picture_id"] in seen_ids or photo["url"] in seen_urls:
                            continue
                        seen_ids.add(photo["picture_id"])
                        seen_urls.add(photo["url"])
                        try:
                            head = await self.fetch(photo["url"], "HEAD")
                        except SourcePaused as exc:
                            problems.append({**photo, "reason": str(exc)})
                            raise
                        mime = head.headers.get(b"Content-Type", b"").decode().split(";")[0]
                        check = {
                            **photo,
                            "http_status": head.status,
                            "content_type": mime,
                            "checked_at": now(),
                            "method": "HEAD without login or Referer",
                        }
                        if head.status == 200 and mime.lower().startswith("image/"):
                            good.append(check)
                        else:
                            problems.append({**check, "reason": "HTTP status or MIME invalid"})
                        if len(good) == 15:
                            break
                    if len(good) == 15 or len(visited) >= total:
                        break
                    page += 1
                if len(good) == 15:
                    break
            status = "满15张" if len(good) == 15 else "不足15张" if good else "无匹配官图"
            if problems and len(good) < 15:
                status = "失败"
            self.finish(target, good, problems, status)
        except Exception as exc:
            problems.append({"reason": str(exc)})
            self.finish(
                target, good, problems, "访问受阻" if isinstance(exc, SourcePaused) else "失败"
            )

    def finish(self, target, good, problems, status):
        save_json(
            self.results / (target["model_id"] + ".json"),
            {
                "target": target,
                "target_sha256": fingerprint(target),
                "status": status,
                "images": good,
                "problems": problems,
                "checked_at": now(),
                "note": "仅最新年款、同动力、官图；不足不以旧年款或实拍补齐；第三方URL不保证永久有效",
            },
        )
        self.logger.info("%s %s %s images", target["model_id"], status, len(good))

    def closed(self, reason):
        report(self.root)


def all_results(root):
    targets = json.loads((root / "targets.json").read_text())["targets"]
    results = []
    for target in targets:
        path = root / "groups" / (target["model_id"] + ".json")
        if path.exists():
            results.append(json.loads(path.read_text()))
        else:
            results.append(
                {
                    "target": target,
                    "target_sha256": fingerprint(target),
                    "images": [],
                    "problems": [],
                    "status": "未处理",
                    "checked_at": "",
                    "note": "任务因来源访问限制暂停；本组尚未检查",
                }
            )
    return results


def report(root):
    results = all_results(root)
    counts = Counter(r["status"] for r in results)
    pending = counts.get("未处理", 0)
    summary = {
        "total": len(results),
        "processed": len(results) - pending,
        "unprocessed": pending,
        "images": sum(len(r["images"]) for r in results),
        "counts": dict(counts),
        "time": now(),
    }
    save_json(root / "summary.json", summary)
    save_json(root / "images.json", results)
    source_only = any(
        p.get("verification") == "source_page_only" for r in results for p in r["images"]
    )
    lines = [
        "# 车型图片URL采集报告",
        "",
        "本批尚未全量完成。"
        if pending or counts.get("访问受阻") or counts.get("失败")
        else "本批范围已全部处理。",
        "",
        f"范围：{len(results)}个model_id；已处理{len(results) - pending}组，未处理{pending}组；已提取URL共{summary['images']}个。",
        "",
        "| 结果 | 分组数 |",
        "|---|---:|",
    ]
    lines += [
        f"| {status} | {counts.get(status, 0)} |"
        for status in ("满15张", "不足15张", "无匹配官图", "失败", "访问受阻", "未处理")
        if not (source_only and status == "无匹配官图")
    ]
    if source_only:
        categories = Counter(p.get("category", "官图") for r in results for p in r["images"])
        covers = sum(
            bool(r["images"]) and all(p.get("scope") == "series" for p in r["images"])
            for r in results
        )
        lines += [
            "",
            f"仅车系封面：{covers}组，飞书年款留空，不能据此确认图片年款或动力。",
            "图片类别：" + "；".join(f"{k} {v}个" for k, v in categories.items()) + "。",
        ]
    lines += [
        "",
        "飞书“车型图片”包含全部分组。未处理行仅是占位，0张不代表源站无图，须同时查看“检查结果”。",
        "",
        "## 暂停与失败原因",
        "",
    ]
    for marker in sorted((root / "pauses").glob("*.json")):
        pause = json.loads(marker.read_text())
        lines.append(
            f"- {pause['url']} 返回HTTP {pause['status']}（UTC {pause['time']}），该来源已暂停。"
        )
    errors = Counter(p["reason"] for r in results if r["status"] == "失败" for p in r["problems"])
    lines += [f"- {reason}：{count}组。" for reason, count in errors.items()]
    if any("NoneType" in reason for reason in errors):
        lines += [
            "- 其中NoneType错误来自下载中间件读取尚未绑定的request；代码已修复并补回归测试。此后来源暂停，这些组仍保留为失败，尚未重新完成采集。"
        ]
    lines += [
        "",
        "## 验证方式与文件",
        "",
        (
            "仅从汽车之家网页提取URL，不访问图片域名、不下载图片；source_page_only表示未做直连HTTP验证。"
            if source_only
            else "仅保存URL，未下载图片文件。可用列表均通过无登录、无Referer的HEAD状态及图片类型检查。"
        ),
        "按用户最新要求，不再进行浏览器或飞书界面核验；CLI回读可核对文本和关联，不能保证点击端表现或永久有效。",
        "逐张URL、图片ID、配置ID、来源页、HTTP状态和时间见同目录images.json。",
        "飞书写入、数量和双向关联核验结果见feishu-verification.json；写入前快照及计划在backups/。",
        "",
        "## 各组明细",
        "",
        "| 车型 | model_id | 目标年款 | 图片数 | 状态 | 详情 |",
        "|---|---|---:|---:|---|---|",
    ]
    for r in results:
        t = r["target"]
        reason = "；".join(p["reason"] for p in r["problems"]).replace("|", "/").replace("\n", " ")
        lines.append(
            f"| {t['name']} | {t['model_id']} | {t['year']} | {len(r['images'])} | {r['status']} | {reason} |"
        )
    (root / "图片采集报告.md").write_text("\n".join(lines) + "\n")
    return summary


def collect(root, model_ids):
    from scrapy.crawler import CrawlerProcess

    from .cli import crawler_settings

    if (root / "STOP").exists():
        raise ValueError(
            "Image run stopped; inspect STOP and source pause evidence before resuming"
        )

    settings = crawler_settings(root, 2, 20)
    settings["DOWNLOADER_MIDDLEWARES"] = {"autohome_catalog.images.ImageRequestGuard": 800}
    settings["LOG_FILE"] = str(root / "images.log")
    process = CrawlerProcess(settings)
    process.crawl(OfficialImagesSpider, root=root, model_ids=model_ids)
    process.start()
    return report(root)
