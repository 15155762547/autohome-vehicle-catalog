import json

import pytest
from scrapy.http import Request, Response

from autohome_catalog.image_sync import assert_image_operation, fields, url_lines
from autohome_catalog.images import (
    OfficialImagesSpider,
    SourcePaused,
    candidates,
    fingerprint,
    image_url,
    page_data,
    photos,
    prepare,
)


def target():
    return dict(
        model_id="AUT-1-PHEV",
        series_id=1,
        name="车",
        brand="品牌",
        power="插电式混合动力",
        year=2027,
        model_record="rec1",
        specs=[11, 12],
    )


def data():
    return {
        "params": {"seriesId": 1, "specId": 11, "typeId": 53},
        "breadInfo": {"seriesid": 1, "specid": 11, "specname": "2027款 测试"},
        "SpecPicList": {
            "speclist": [
                {
                    "specid": 11,
                    "total": 1,
                    "piclist": [
                        {
                            "picid": 1,
                            "specid": 11,
                            "claid": 53,
                            "picpath": "//car2.autoimg.cn/test.jpg",
                        }
                    ],
                }
            ]
        },
    }


def test_latest_year_and_power_separate(tmp_path, monkeypatch):
    import autohome_catalog.images as module

    monkeypatch.setattr(module, "assert_external", lambda p: p)
    rows = []
    for code, year, spec in [("BEV", 2025, 1), ("BEV", 2027, 2), ("PHEV", 2026, 3)]:
        rows.append(
            {
                "fields": {
                    "model_id": "AUT-1-" + code,
                    "配置名称": f"{year}款 A",
                    "来源车系ID": "1",
                    "车系名称": "车",
                    "品牌": "牌",
                    "能源类型": code,
                    "所属车系": [{"id": "rec1"}],
                    "来源配置ID": str(spec),
                }
            }
        )
    source = tmp_path / "rows.json"
    source.write_text(json.dumps(rows))
    assert prepare(source, tmp_path / "run") == {"groups": 2, "candidates": 2}
    targets = json.loads((tmp_path / "run/targets.json").read_text())["targets"]
    assert targets[0]["specs"] == [2]
    assert targets[1]["specs"] == [3]


def test_gallery_identity_category_year_and_empty():
    d = data()
    html = '<script id="__NEXT_DATA__">' + json.dumps({"props": {"pageProps": d}}) + "</script>"
    assert page_data(html, 1, 11) == d
    with pytest.raises(ValueError):
        page_data(html, 2, 11)
    rows, total = photos(d, target(), 11, "source")
    assert total == 1 and rows[0]["url"] == "https://car2.autoimg.cn/test.jpg"
    d["SpecPicList"]["speclist"] = []
    assert photos(d, target(), 11, "source") == ([], 0)
    d["breadInfo"]["specname"] = "2026款 测试"
    with pytest.raises(ValueError):
        photos(d, target(), 11, "source")


def test_ranking_checks_missing_and_power_scope():
    d = {
        "specList": [
            {"year": 2027, "list": [{"specid": 12, "count": 10}, {"specid": 99, "count": 1000}]}
        ]
    }
    assert candidates(d, target()) == [12, 11]
    d["specList"][0]["year"] = 2026
    with pytest.raises(ValueError):
        candidates(d, target())


@pytest.mark.parametrize(
    "url", ["https://evil.cn/a.jpg", "file:///a", "https://car2.autoimg.cn@evil.cn/a.jpg"]
)
def test_url_allowlist(url):
    with pytest.raises(ValueError):
        image_url(url)


def test_multiline_roundtrip_and_usable_validation():
    assert url_lines("[a](https://car2.autoimg.cn/a.jpg)\nhttps://car3.autoimg.cn/b.jpg") == [
        "https://car2.autoimg.cn/a.jpg",
        "https://car3.autoimg.cn/b.jpg",
    ]
    t = target()
    r = {
        "target": t,
        "target_sha256": fingerprint(t),
        "images": [],
        "status": "无匹配官图",
        "problems": [],
        "note": "strict",
        "checked_at": "now",
    }
    assert fields(r)["图片数"] == 0
    r["images"] = [
        {
            "url": "https://car2.autoimg.cn/a.jpg",
            "http_status": 403,
            "content_type": "image/jpeg",
            "spec_id": 11,
        }
    ]
    with pytest.raises(ValueError):
        fields(r)


def test_403_pauses_source(tmp_path):
    spider = object.__new__(OfficialImagesSpider)
    spider.root, spider.paused = tmp_path, set()
    r = Response(
        "https://car2.autoimg.cn/a.jpg",
        status=403,
        request=Request("https://car2.autoimg.cn/a.jpg", method="HEAD"),
    )
    with pytest.raises(SourcePaused):
        spider.check(r)
    assert spider.paused == {"car2.autoimg.cn"}
    assert (tmp_path / "pauses/car2.autoimg.cn.json").exists()


def test_image_write_scope():
    config = {"tables": {"models": "models", "trims": "trims", "images": "images"}}
    assert_image_operation(config, "+record-batch-create", ["--table-id", "images"])
    with pytest.raises(ValueError):
        assert_image_operation(config, "+record-batch-create", ["--table-id", "trims"])


def test_downloader_guard_handles_unbound_response(tmp_path):
    from autohome_catalog.images import ImageRequestGuard

    spider = object.__new__(OfficialImagesSpider)
    spider.root, spider.paused = tmp_path, set()
    req = Request("https://car2.autoimg.cn/a.jpg", method="HEAD")
    response = Response(req.url, status=200, headers={"Content-Type": "image/jpeg"})
    guard = ImageRequestGuard()
    assert guard.process_response(req, response, spider) is response
    spider.paused.add("car2.autoimg.cn")
    with pytest.raises(SourcePaused):
        guard.process_request(req, spider)
    spider.paused.clear()
    with pytest.raises(ValueError):
        guard.process_request(Request(req.url), spider)  # Media GET is forbidden.


def test_pagination_dedup_and_resume(tmp_path, monkeypatch):
    import asyncio

    from scrapy.http import HtmlResponse

    from autohome_catalog.images import gallery_url

    t = {**target(), "specs": [11]}
    (tmp_path / "targets.json").write_text(json.dumps({"targets": [t]}))
    spider = OfficialImagesSpider(root=tmp_path)
    inventory = {**data(), "specList": [{"year": 2027, "list": [{"specid": 11, "count": 3}]}]}

    def response(url, value):
        html = (
            '<script id="__NEXT_DATA__">'
            + json.dumps({"props": {"pageProps": value}})
            + "</script>"
        )
        return HtmlResponse(url, body=html.encode(), encoding="utf-8", request=Request(url))

    visited = []

    async def fetch(url, method="GET"):
        visited.append((url, method))
        if method == "HEAD":
            return Response(url, status=200, headers={"Content-Type": "image/jpeg"})
        d = data()
        page = int(url.rsplit("-", 1)[1].split(".")[0])
        ids = [1, 2] if page == 1 else [2, 3]
        d["SpecPicList"]["speclist"][0].update(
            total=3,
            piclist=[
                {
                    "picid": n,
                    "specid": 11,
                    "claid": 53,
                    "picpath": f"https://car2.autoimg.cn/{n}.jpg",
                }
                for n in ids
            ],
        )
        return response(url, d)

    monkeypatch.setattr(spider, "fetch", fetch)
    asyncio.run(spider.collect(response(gallery_url(1), inventory), t))
    result = json.loads((tmp_path / "groups/AUT-1-PHEV.json").read_text())
    assert len(result["images"]) == 3 and result["status"] == "不足15张"
    assert len([x for x in visited if x[1] == "GET"]) == 2
    assert len([x for x in visited if x[1] == "HEAD"]) == 3

    async def starts():
        return [r async for r in spider.start()]

    assert asyncio.run(starts()) == []
    result["target_sha256"] = "modified"
    (tmp_path / "groups/AUT-1-PHEV.json").write_text(json.dumps(result))
    with pytest.raises(ValueError, match="Resume target changed"):
        asyncio.run(starts())


def test_repeat_plan_and_cli_readback_urls():
    from autohome_catalog.feishu import diff_records
    from autohome_catalog.image_sync import normalize

    desired = [
        {
            "key": "AUT-1-BEV",
            "fields": {
                "model_id": "AUT-1-BEV",
                "官图URL清单": "https://car2.autoimg.cn/1.jpg\nhttps://car2.autoimg.cn/2.jpg",
                "图片数": 2,
                "所属车系": [{"id": "rec1"}],
            },
        }
    ]
    current = [
        {
            "record_id": "rec2",
            "fields": {
                **desired[0]["fields"],
                "官图URL清单": "[图1](https://car2.autoimg.cn/1.jpg)\n[图2](https://car2.autoimg.cn/2.jpg)",
            },
        }
    ]
    assert diff_records(desired, normalize(current), "model_id") == {"create": [], "update": {}}


def test_source_url_category_order_and_cdn_never_requested(tmp_path):
    from autohome_catalog.image_urls import GalleryURLGuard, any_photos

    t = target()

    def category(cid, name, pic):
        return {
            "claid": cid,
            "seriesid": 1,
            "name": name,
            "list": [{"picid": pic, "specid": 11, "picpath": f"https://car2.autoimg.cn/{pic}.jpg"}],
        }

    rows = any_photos(
        {
            "SeriesPicList": {
                "picinfo": {"callist": [category(1, "外观", 1), category(53, "官图", 2)]}
            }
        },
        t,
        11,
        "source",
    )
    assert [r["category"] for r in rows] == ["官图", "外观"]
    spider = type("Spider", (), {"paused": set(), "root": tmp_path})()
    with pytest.raises(ValueError, match="never contacts image hosts"):
        GalleryURLGuard().process_request(Request(rows[0]["url"]), spider)
    with pytest.raises(ValueError):
        GalleryURLGuard().process_request(Request(rows[0]["url"], method="HEAD"), spider)


def test_source_only_rows_require_explicit_policy_and_series_year_blank():
    t = target()
    photo = {
        "url": "https://car2.autoimg.cn/a.jpg",
        "scope": "series",
        "spec_id": None,
        "category": "车系封面",
        "source_page": "https://www.autohome.com.cn/",
        "verification": "source_page_only",
    }
    result = {
        "target": t,
        "target_sha256": fingerprint(t),
        "images": [photo],
        "status": "不足15张",
        "problems": [],
        "note": "source",
        "checked_at": "now",
    }
    with pytest.raises(ValueError):
        fields(result)
    result["policy"] = "source-urls-official-first-v1"
    f = fields(result)
    assert f["年款"] == "" and f["图片类别"] == "车系封面"
    assert "未直连验证" in f["验证方式"]
    assert f["图片URL清单"] == photo["url"]


def test_cover_fallback_uses_hash_proven_protocol_relative_url(tmp_path):
    import hashlib

    from autohome_catalog.image_urls import POLICY, finalize_urls

    root, old, source = tmp_path / "run", tmp_path / "old", tmp_path / "catalog"
    (root / "groups").mkdir(parents=True)
    (source / "series").mkdir(parents=True)
    old.mkdir()
    t = target()
    (root / "targets.json").write_text(json.dumps({"targets": [t]}))
    raw = json.dumps({"seriesid": 1, "seriesimg": "//car2.autoimg.cn/cover.png"}).encode()
    (source / "catalog.json").write_bytes(raw)
    packet = {
        "series": {
            "seriesimg": "https://car2.autoimg.cn/cover.png",
            "catalog_source": {
                "file": "catalog.json",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "url": "https://www.autohome.com.cn/search",
                "fetched_at": "then",
            },
        }
    }
    (source / "series/1.json").write_text(json.dumps(packet))
    failed = {
        "target": t,
        "target_sha256": fingerprint(t),
        "policy": POLICY,
        "images": [],
        "status": "失败",
        "problems": [{"reason": "gallery unavailable"}],
        "note": "source",
        "checked_at": "now",
    }
    (root / "groups/AUT-1-PHEV.json").write_text(json.dumps(failed))
    summary = finalize_urls(root, old, source)
    assert summary["unprocessed"] == 0 and summary["images"] == 1
    result = json.loads((root / "groups/AUT-1-PHEV.json").read_text())
    assert result["images"][0]["scope"] == "series"
    assert result["images"][0]["url"] == "https://car2.autoimg.cn/cover.png"
    assert (root / "recovered/AUT-1-PHEV.json").exists()
    (source / "catalog.json").write_text("modified")
    from autohome_catalog.image_urls import SourceURLsSpider

    with pytest.raises(ValueError, match="hash differs"):
        SourceURLsSpider(root, old, source).cover(t)
