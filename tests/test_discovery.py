import hashlib
import json

import pytest
from scrapy.http import Request, TextResponse

from autohome_catalog.discovery import load_manifest
from autohome_catalog.spider import CatalogSpider


def test_manifest_validates_membership_hash_and_exact_configurations(tmp_path):
    rows = []
    for state in (2, 3):
        row = {
            "seriesid": state,
            "seriesname": str(state),
            "seriesstate": 20 if state == 2 else 10,
            "specids": [state * 10],
            "specnum": 1,
            "newenergy": 0,
        }
        raw = json.dumps({"result": {"seriesgrouplist": [row]}}).encode()
        (tmp_path / f"{state}.raw").write_bytes(raw)
        rows.append(
            {
                **row,
                "catalog_state": state,
                "scope_status": {2: "在售", 3: "预售"}[state],
                "catalog_source": {
                    "file": f"{state}.raw",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "url": f"https://www.autohome.com.cn/web-main/car/search/searchcar?searchtype=6&state={state}",
                },
            }
        )
    path = tmp_path / "manifest.json"
    manifest = {
        "phase": "complete",
        "errors": [],
        "overlapping_series": [],
        "counts": {"2": 1, "3": 1},
        "rows": rows,
    }
    path.write_text(json.dumps(manifest))
    assert len(load_manifest(path)["rows"]) == 2  # Search membership overrides unreliable NEV flag.
    manifest["rows"][1]["specids"] = [999]
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="captured"):
        load_manifest(path)


def test_spec_selection_uses_only_manifest_ids_and_requires_them_all(tmp_path):
    spider = CatalogSpider(tmp_path)
    request = Request("https://www.autohome.com.cn/web-main/car/spec/getSpecList")
    body = {
        "returncode": 0,
        "result": {
            "seriesid": 2,
            "items": [
                {"id": 20, "specname": "选中", "specstate": 20},
                {"id": 21, "specname": "不在清单", "specstate": 40},
            ],
        },
    }
    response = TextResponse(
        request.url, request=request, body=json.dumps(body).encode(), encoding="utf8"
    )
    response.meta["source_receipt"] = {}
    series = {"seriesid": 2, "catalog_state": 2, "scope_status": "在售", "specids": [20]}
    requests = list(spider.parse_spec_list(response, series))
    assert requests[0].cb_kwargs["context"]["spec_list"] == [body["result"]["items"][0]]
    with pytest.raises(ValueError, match="missing"):
        list(spider.parse_spec_list(response, {**series, "specids": [999]}))


def test_discovery_cannot_report_complete_for_duplicate_or_missing_pages(tmp_path):
    from types import SimpleNamespace

    from autohome_catalog.discovery import DiscoverySpider

    spider = DiscoverySpider(tmp_path, expected_on_sale=2, expected_presale=0)
    spider.crawler = SimpleNamespace(stats=SimpleNamespace(get_stats=lambda: {}))
    row = {"seriesid": 2, "seriesname": "重复", "seriesstate": 20, "specids": [20], "specnum": 1}
    spider.pages = {2: {1: {"rows": [row, row], "source": {}}}, 3: {}}
    spider.closed_report(spider, "finished")
    result = json.loads((tmp_path / "manifest.json").read_text())
    assert result["phase"] == "complete_with_issues"
    assert result["errors"]


def test_callback_errors_are_in_progress_and_cannot_hide_behind_empty_download_errors(tmp_path):
    from types import SimpleNamespace

    spider = CatalogSpider(tmp_path, catalog_rows=[{"seriesid": 8617}])
    request = Request(
        "https://www.autohome.com.cn/example", cb_kwargs={"series": {"seriesid": 8617}}
    )
    response = TextResponse(request.url, request=request, body=b"{}", encoding="utf8")
    spider.callback_failed(
        SimpleNamespace(value=ValueError("missing parameter data")), response, spider
    )
    progress = json.loads((tmp_path / "progress.json").read_text())
    assert progress["errors"][0]["series_id"] == 8617
    assert progress["completed_series"] == 0
