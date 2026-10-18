import json

import pytest

from autohome_catalog.historical_images import collect, image_patch, matching_power, menu


def test_historical_menu_excludes_latest_and_empty_and_prefers_recent_year():
    data = {
        "specOtherList": [
            {"year": 2025, "list": [{"specid": 1, "count": 100}]},
            {"year": 2024, "list": [{"specid": 2, "count": 4}, {"specid": 3, "count": 8}]},
            {"year": 2019, "list": [{"specid": 4, "count": 100}, {"specid": 5, "count": 0}]},
        ]
    }
    assert [r["specid"] for r in menu(data, 2025)] == [3, 2, 4]


def payload():
    return {
        "returncode": 0,
        "result": {
            "bread": {"seriesid": 2761},
            "titlelist": [{"items": [{"itemname": "能源类型", "titleid": 6}]}],
            "datalist": [
                {
                    "specid": 39780,
                    "specname": "2019款 尊贵型",
                    "paramconflist": [{"titleid": 6, "itemname": "纯电动"}],
                }
            ],
        },
    }


def test_exact_configuration_year_series_and_power_identity():
    target = {"series_id": 2761, "power": "纯电动"}
    assert matching_power(payload(), target, 39780, 2019)
    assert not matching_power(payload(), {**target, "power": "插电式混合动力"}, 39780, 2019)
    for bad_target, spec, year in [
        ({**target, "series_id": 2}, 39780, 2019),
        (target, 123, 2019),
        (target, 39780, 2025),
    ]:
        with pytest.raises(ValueError):
            matching_power(payload(), bad_target, spec, year)


def test_historical_patch_keeps_actual_year_and_excludes_deleted_status():
    result = {
        "target": {"year": 2025, "power": "纯电动"},
        "image_year": 2019,
        "images": [
            {
                "url": "https://car3.autoimg.cn/a.jpg",
                "spec_id": 39780,
                "source_page": "https://www.autohome.com.cn/2761/",
            }
        ],
        "checked_at": "2026-10-18T15:30:00+00:00",
    }
    patch = image_patch(result)
    assert patch["图片年款"] == "2019"
    assert "2025款" in patch["图片说明"] and "2019款" in patch["图片说明"]
    assert "图片检查结果" not in patch
    assert json.loads(patch["图片URL清单"]) == ["https://car3.autoimg.cn/a.jpg"]
    with pytest.raises(ValueError):
        image_patch({**result, "images": result["images"] * 2})


def test_collection_does_not_fetch_images_or_cross_power_or_mix_years(monkeypatch):
    import autohome_catalog.historical_images as module

    data = {
        "specOtherList": [
            {"year": 2024, "list": [{"specid": 20, "count": 30}]},
            {"year": 2019, "list": [{"specid": 39780, "count": 10}]},
            {"year": 2018, "list": [{"specid": 30, "count": 20}]},
        ]
    }
    monkeypatch.setattr(module, "page_data", lambda *args: data)
    monkeypatch.setattr(module, "matching_power", lambda d, t, spec, y: spec == 39780)
    monkeypatch.setattr(
        module,
        "photos",
        lambda d, t, s, u: (
            [
                {
                    "picture_id": 1,
                    "spec_id": s,
                    "url": "https://car3.autoimg.cn/a.jpg",
                    "source_page": u,
                }
            ]
            * 2,
            2,
        ),
    )

    class Fetcher:
        def get(self, url):
            assert "autoimg" not in url and "specid=30" not in url
            return "{}"

    result = collect(Fetcher(), {"series_id": 2761, "power": "纯电动", "year": 2025})
    assert result["image_year"] == 2019 and len(result["images"]) == 1
