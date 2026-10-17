"""Collect source-displayed URLs without contacting image hosts."""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import scrapy
from scrapy.http import HtmlResponse
from scrapy.utils.defer import maybe_deferred_to_future

from .images import (
    OfficialImagesSpider,
    SourcePaused,
    candidates,
    fingerprint,
    gallery_url,
    image_url,
    now,
    page_data,
    photos,
    report,
)
from .storage import assert_external, save_json

POLICY = "source-urls-official-first-v1"


def all_url(series, spec="x"):
    return gallery_url(series, spec).replace("-53-x-x-x-x-", "-x-x-x-x-x-")


def any_data(text, target, spec=None):
    match = re.search(r'<script\b[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', text, re.S)
    if not match:
        raise ValueError("Missing gallery data")
    d = json.loads(match[1])["props"]["pageProps"]
    b = d["breadInfo"]
    if int(b.get("seriesid") or 0) != target["series_id"]:
        raise ValueError("Wrong series")
    if spec is not None:
        if int(b.get("specid") or 0) != spec or not b.get("specname", "").startswith(
            str(target["year"]) + "款"
        ):
            raise ValueError("Wrong specification or year")
    return d


def any_photos(d, target, spec, url):
    rows = []
    categories = (d.get("SeriesPicList") or {}).get("picinfo", {}).get("callist", [])
    for category in sorted(categories, key=lambda x: (x["claid"] != 53, x.get("sort", 100))):
        if int(category.get("seriesid") or 0) != target["series_id"]:
            raise ValueError("Wrong category series")
        for photo in category.get("list", []):
            if int(photo.get("specid") or 0) != spec or int(photo.get("mediatype") or 0) != 0:
                continue
            rows.append(
                dict(
                    picture_id=int(photo["picid"]),
                    spec_id=spec,
                    url=image_url(photo["picpath"]),
                    source_page=url,
                    category_id=category["claid"],
                    category=category["name"],
                    scope="configuration",
                    verification="source_page_only",
                    checked_at=now(),
                )
            )
    return rows


class GalleryURLGuard:
    def process_request(self, request, spider):
        p = urlsplit(request.url)
        if p.hostname != "www.autohome.com.cn" or request.method != "GET":
            raise ValueError("URL extraction never contacts image hosts")
        if p.path != "/robots.txt" and not re.fullmatch(
            r"/cars/imglist-x-x-\d+-(?:x|\d+)-(?:x|53)-x-x-x-x-\d+\.html", p.path
        ):
            raise ValueError("Unexpected gallery path")
        if p.hostname in spider.paused or (spider.root / "STOP").exists():
            raise SourcePaused("Gallery source paused")

    def process_response(self, request, response, spider):
        spider.check(response.replace(request=request))
        return response


class SourceURLsSpider(OfficialImagesSpider):
    name = "source_image_urls"

    def __init__(self, root, official_root, source_root, **kwargs):
        super().__init__(root, **kwargs)
        self.official_root = assert_external(Path(official_root))
        self.source_root = assert_external(Path(source_root))
        self.cache = {}
        for receipt_path in (self.official_root / "sources").glob("*.json"):
            receipt = json.loads(receipt_path.read_text())
            if receipt.get("status") == 200:
                self.cache[receipt["url"]] = receipt

    async def start(self):
        for t in self.targets:
            path = self.results / (t["model_id"] + ".json")
            if path.exists() and json.loads(path.read_text()).get("policy") == POLICY:
                continue
            prior = self.official_root / "groups" / (t["model_id"] + ".json")
            if prior.exists():
                r = json.loads(prior.read_text())
                if r["target_sha256"] == fingerprint(t) and len(r["images"]) == 15:
                    for photo in r["images"]:
                        photo.update(category_id=53, category="官图", scope="configuration")
                    self.finish(t, r["images"], [], "满15张")
                    continue
            yield scrapy.Request(
                gallery_url(t["series_id"]),
                callback=self.collect,
                cb_kwargs={"target": t},
                meta={"handle_httpstatus_all": True},
                dont_filter=True,
                errback=self.initial_error,
            )

    async def fetch(self, url, method="GET"):
        if method != "GET" or urlsplit(url).hostname != "www.autohome.com.cn":
            raise ValueError("No media requests in URL extraction")
        receipt = self.cache.get(url)
        if receipt:
            path = self.official_root / "sources" / (receipt["sha256"] + ".html")
            if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == receipt["sha256"]:
                return HtmlResponse(
                    url, body=path.read_bytes(), encoding="utf-8", request=scrapy.Request(url)
                )
        res = await maybe_deferred_to_future(
            self.crawler.engine.download(
                scrapy.Request(url, dont_filter=True, meta={"handle_httpstatus_all": True})
            )
        )
        digest = hashlib.sha256(res.body).hexdigest()
        (self.sources / (digest + ".html")).write_bytes(res.body)
        save_json(
            self.sources / (hashlib.sha256(url.encode()).hexdigest() + ".json"),
            {"url": url, "status": res.status, "sha256": digest, "checked_at": now()},
        )
        return res

    def cover(self, t):
        packet = json.loads((self.source_root / "series" / f"{t['series_id']}.json").read_text())
        source = packet["series"]["catalog_source"]
        raw = self.source_root / source["file"]
        if hashlib.sha256(raw.read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("Catalog cover evidence hash differs")
        url = image_url(packet["series"]["seriesimg"])
        if url.removeprefix("https:") not in raw.read_text():
            raise ValueError("Cover URL absent from catalog evidence")
        return dict(
            url=url,
            picture_id=None,
            spec_id=None,
            category="车系封面",
            category_id=None,
            scope="series",
            source_page=source["url"],
            source_sha256=source["sha256"],
            checked_at=source["fetched_at"],
            verification="source_page_only",
        )

    async def collect(self, response, target):
        good, problems = [], []
        ids, urls = set(), set()

        def add(rows):
            for photo in rows:
                if photo["url"] in urls or (photo["picture_id"] and photo["picture_id"] in ids):
                    continue
                if len(good) == 15:
                    break
                urls.add(photo["url"])
                ids.add(photo["picture_id"])
                good.append(photo)

        try:
            self.check(response)
            inventory = (
                page_data(response.text, target["series_id"]) if response.status == 200 else None
            )
            ordered = candidates(inventory, target) if inventory else target["specs"]
            # Current official menus rank candidate configurations by official photo count.
            for spec in ordered:
                if len(good) == 15:
                    break
                for page in range(1, 3):
                    url = gallery_url(target["series_id"], spec, page)
                    res = await self.fetch(url)
                    if res.status != 200:
                        problems.append({"reason": f"官图页HTTP {res.status}", "source_page": url})
                        break
                    d = page_data(res.text, target["series_id"], spec)
                    rows, total = photos(d, target, spec, url)
                    add(
                        [
                            {
                                **p,
                                "category_id": 53,
                                "category": "官图",
                                "scope": "configuration",
                                "verification": "source_page_only",
                                "checked_at": now(),
                            }
                            for p in rows
                        ]
                    )
                    if len(good) == 15 or total <= len(rows) or not rows:
                        break
            if len(good) < 15:
                url = all_url(target["series_id"])
                res = await self.fetch(url)
                if res.status == 200:
                    d = any_data(res.text, target)
                    ordered = candidates(d, target)
                for spec in ordered:
                    if len(good) == 15:
                        break
                    url = all_url(target["series_id"], spec)
                    res = await self.fetch(url)
                    if res.status != 200:
                        continue
                    d = any_data(res.text, target, spec)
                    add(any_photos(d, target, spec, url))
            if not good:
                add([self.cover(target)])
            self.finish(target, good, problems, "满15张" if len(good) == 15 else "不足15张")
        except Exception as exc:
            problems.append({"reason": str(exc)})
            # A failed gallery must not discard a source-proven series cover.
            if not good:
                try:
                    add([self.cover(target)])
                except Exception as cover_error:
                    problems.append({"reason": str(cover_error)})
            self.finish(
                target,
                good,
                problems,
                "不足15张" if good else "访问受阻" if isinstance(exc, SourcePaused) else "失败",
            )

    def finish(self, target, good, problems, status):
        save_json(
            self.results / (target["model_id"] + ".json"),
            {
                "target": target,
                "target_sha256": fingerprint(target),
                "policy": POLICY,
                "images": good,
                "problems": problems,
                "status": status,
                "checked_at": now(),
                "note": "官图优先，其次同配置其他图库；车系封面仅作最后兜底，不能确认图片年款/动力。仅提取源站URL，不请求图片域名；未直连验证不代表HTTP可用。",
            },
        )
        self.logger.info("%s %s %s URLs", target["model_id"], status, len(good))


def collect_urls(root, official_root, source_root):
    from scrapy.crawler import CrawlerProcess

    from .cli import crawler_settings

    root, official_root = assert_external(root), assert_external(official_root)
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "targets.json").exists():
        save_json(root / "targets.json", json.loads((official_root / "targets.json").read_text()))
    if (root / "STOP").exists():
        raise ValueError("URL extraction stopped")
    settings = crawler_settings(root, 2, 20)
    settings["DOWNLOADER_MIDDLEWARES"] = {"autohome_catalog.image_urls.GalleryURLGuard": 800}
    settings["LOG_FILE"] = str(root / "url-extraction.log")
    process = CrawlerProcess(settings)
    process.crawl(SourceURLsSpider, root=root, official_root=official_root, source_root=source_root)
    process.start()
    return finalize_urls(root, official_root, source_root)


def finalize_urls(root, official_root, source_root):
    """Repair source-cover normalization offline and retain prior HTTP evidence."""
    spider = SourceURLsSpider(root, official_root, source_root)
    recovered = 0
    for target in spider.targets:
        path = spider.results / (target["model_id"] + ".json")
        if not path.exists():
            continue
        result = json.loads(path.read_text())
        if result["target_sha256"] != fingerprint(target):
            raise ValueError("Final URL result target changed")
        if not result["images"]:
            cover = spider.cover(target)
            save_json(spider.root / "recovered" / path.name, result)
            result.update(images=[cover], status="不足15张")
            result["problems"] = [
                p
                for p in result["problems"]
                if p["reason"] != "Cover URL absent from catalog evidence"
            ]
            result["note"] += "；图库未取到图片，使用经来源哈希核对的车系封面。"
            recovered += 1
        old_path = spider.official_root / "groups" / path.name
        if old_path.exists():
            old = json.loads(old_path.read_text())
            if old["target_sha256"] != fingerprint(target):
                raise ValueError("Prior evidence target mismatch")
            verified = {
                p["url"]: p
                for p in old["images"]
                if p.get("http_status") == 200 and p.get("content_type", "").startswith("image/")
            }
            for photo in result["images"]:
                prior = verified.get(photo["url"])
                if prior:
                    photo.update(
                        {
                            k: prior[k]
                            for k in ["http_status", "content_type", "method", "checked_at"]
                        }
                    )
                    photo["verification"] = "http200_image_type"
        save_json(path, result)
    summary = report(spider.root)
    summary["offline_cover_recoveries"] = recovered
    save_json(spider.root / "finalization.json", summary)
    return summary
