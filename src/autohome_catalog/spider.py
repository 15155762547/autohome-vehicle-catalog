"""Bounded brand directory -> exact configuration list -> parameter responses."""

import json
from pathlib import Path
from urllib.parse import urlencode

import scrapy
from scrapy import signals
from scrapy.exceptions import IgnoreRequest
from scrapy.utils.defer import deferred_from_coro

from .models import ParameterResponse, SpecList
from .parse import choose_series, parse_parameters
from .storage import assert_run_allowed, capture, guard_response, save_json, validate_request_url

BASE = "https://www.autohome.com.cn/web-main/car/"


class EvidenceMiddleware:
    def process_request(self, request, spider):
        validate_request_url(request.url)
        try:
            assert_run_allowed(spider.root)
        except ValueError as error:
            raise IgnoreRequest(str(error)) from error

    def process_response(self, request, response, spider):
        receipt = capture(spider.root, response, request)
        request.meta["source_receipt"] = receipt
        if response.status in (500, 502, 503, 504):
            return response  # RetryMiddleware handles one bounded retry.
        try:
            guard_response(
                response.status, response.body, response.headers.get("Content-Type", b"").decode()
            )
        except ValueError as error:
            save_json(spider.root / "PAUSED.json", {"reason": str(error), "source": receipt})
            save_json(
                spider.root.parent / "ACCESS_PAUSED.json", {"reason": str(error), "source": receipt}
            )
            deferred_from_coro(spider.crawler.engine.close_spider_async(reason="access_paused"))
            raise IgnoreRequest(str(error)) from error
        return response


class CatalogSpider(scrapy.Spider):
    name = "catalog"
    allowed_domains = ["www.autohome.com.cn"]

    def __init__(self, root, brands, series_ids=(), presale_series=(), **kwargs):
        super().__init__(**kwargs)
        self.root = Path(root)
        self.brands = set(brands)
        self.series_ids = set(series_ids)
        self.presale_series = set(presale_series)
        self.directory, self.review, self.errors = [], [], []
        self.completed = set()

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider.closed_report, signal=signals.spider_closed)
        return spider

    async def start(self):
        for brand in sorted(self.brands):
            yield scrapy.Request(
                BASE + f"series/getserieslistbybrandid?brandid={brand}",
                callback=self.parse_brand,
                errback=self.failed,
                cb_kwargs={"brand_id": brand},
            )

    def source_json(self, response):
        envelope = json.loads(response.text)
        if envelope.get("returncode") != 0 or not isinstance(envelope.get("result"), dict):
            raise ValueError("Source envelope changed or source rejected request")
        return envelope["result"]

    def parse_brand(self, response, brand_id):
        result = self.source_json(response)
        rows = result["serieslist"]
        sale_options = [
            option
            for option in result["searchoption1"]
            if option.get("key") == "seriesstate" and option.get("name") == "在售"
        ]
        if len(sale_options) != 1:
            raise ValueError("Source in-sale state mapping is missing or ambiguous")
        chosen, review = choose_series(rows, self.presale_series, sale_options[0]["value"])
        if self.series_ids:
            chosen = [r for r in chosen if r["seriesid"] in self.series_ids]
            review = [r for r in review if r["seriesid"] in self.series_ids]
        self.review.extend(review)
        self.directory.extend(
            {**r, "brand_id": brand_id, "scope_status": "excluded"}
            for r in rows
            if r["seriesid"] not in {x["seriesid"] for x in chosen}
        )
        for row in chosen:
            row = {**row, "brand_id": brand_id, "catalog_source": response.meta["source_receipt"]}
            self.directory.append(row)
            state = 3 if row["scope_status"] == "预售" else 2
            url = (
                BASE
                + "spec/getSpecList?"
                + urlencode(
                    {"searchtype": 6, "seriesid": row["seriesid"], "state": state, "orderid": 0}
                )
            )
            yield scrapy.Request(
                url, callback=self.parse_spec_list, errback=self.failed, cb_kwargs={"series": row}
            )

    def parse_spec_list(self, response, series):
        listing = SpecList.model_validate(self.source_json(response))
        sid = series["seriesid"]
        if listing.seriesid != sid:
            raise ValueError("Wrong series in configuration list")
        items = [s.model_dump() for s in listing.items]
        if len({s["id"] for s in items}) != len(items):
            raise ValueError("Duplicate configuration IDs in list")
        permitted_states = {10} if series["scope_status"] == "预售" else {20, 30}
        if any(s["specstate"] not in permitted_states for s in items):
            raise ValueError("Unexpected configuration sales state")
        if not items:
            self.errors.append({"series_id": sid, "reason": "No eligible configurations"})
            return
        context = {
            "series": series,
            "spec_list": items,
            "spec_list_source": response.meta["source_receipt"],
        }
        url = (
            BASE
            + "param/getParamConf?"
            + urlencode(
                {
                    "mode": 1,
                    "site": 1,
                    "seriesid": sid,
                    "specids": ",".join(str(s["id"]) for s in items),
                }
            )
        )
        yield scrapy.Request(
            url,
            callback=self.parse_parameters_response,
            errback=self.failed,
            cb_kwargs={"context": context},
        )

    def parse_parameters_response(self, response, context):
        sid = context["series"]["seriesid"]
        payload = self.source_json(response)
        ids = {s["id"] for s in context["spec_list"]}
        parse_parameters(payload, sid, ids)
        parsed = ParameterResponse.model_validate(payload)
        expected_states = {s["id"]: s["specstate"] for s in context["spec_list"]}
        if any(
            s.specstatus != expected_states[s.specid] for s in parsed.datalist if s.specid in ids
        ):
            raise ValueError(
                "Configuration sales state changed between list and parameter response"
            )
        packet = {
            **context,
            "parameter_source": response.meta["source_receipt"],
            "payload": payload,
        }
        save_json(self.root / f"series/{sid}.json", packet)
        self.completed.add(sid)

    def failed(self, failure):
        self.errors.append({"url": failure.request.url, "reason": str(failure.value)})

    def closed_report(self, spider, reason):
        stats = self.crawler.stats.get_stats()
        stats = {
            k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v
            for k, v in stats.items()
        }
        expected = {s["seriesid"] for s in self.directory if s["scope_status"] != "excluded"}
        save_json(self.root / "directory.json", {"rows": self.directory, "review": self.review})
        exceptions = any(k.startswith("spider_exceptions/") and v for k, v in stats.items())
        save_json(
            self.root / "status.json",
            {
                "phase": "complete"
                if reason == "finished"
                and not self.errors
                and expected == self.completed
                and not exceptions
                else "complete_with_issues",
                "close_reason": reason,
                "expected_series": sorted(expected),
                "completed_series": sorted(self.completed),
                "missing_series": sorted(expected - self.completed),
                "requested_but_not_eligible": sorted(self.series_ids - expected),
                "errors": self.errors,
                "stats": stats,
            },
        )
