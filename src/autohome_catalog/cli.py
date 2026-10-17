"""Explicit, bounded commands. Network collection and writes are separate."""

import argparse
import json
import os
import re
import shutil
from pathlib import Path

from .export import export_run
from .storage import assert_external, assert_run_allowed, save_json


def main():
    parser = argparse.ArgumentParser(description="汽车之家车系/配置采集：Scrapy + Pydantic")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(
            os.environ.get(
                "AUTOHOME_DATA_ROOT", "/Volumes/奥睿科1TB/代码程序/autohome-vehicle-catalog-data"
            )
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    discover = commands.add_parser("discover", help="保存新能源在售及预售目录的完整分页清单")
    discover.add_argument("--expected-on-sale", type=int, required=True)
    discover.add_argument("--expected-presale", type=int, required=True)
    discover.add_argument("--run", required=True)
    discover.set_defaults(concurrency=2, timeout=20)
    crawl = commands.add_parser("crawl", help="按已验证目录清单采集，或仅采指定品牌／车系")
    crawl.add_argument("--brand", type=int, action="append", default=[])
    crawl.add_argument("--manifest", type=Path, help="已验证的新能源在售及预售目录清单")
    crawl.add_argument("--series", type=int, action="append", default=[])
    crawl.add_argument("--presale-series", type=int, action="append", default=[])
    crawl.add_argument("--run", required=True)
    crawl.add_argument("--concurrency", type=int, choices=[1, 2, 4], default=2)
    crawl.add_argument("--timeout", type=int, choices=[15, 20, 30], default=20)
    prepare_full = commands.add_parser(
        "prepare-full-sync", help="把完整字段按300列预算划分为横向扩展批次"
    )
    prepare_full.add_argument("--run", required=True)
    sync_full = commands.add_parser(
        "sync-full", help="按已准备批次向指定实验Base写入并核验，包含新增扩展表"
    )
    sync_full.add_argument("--run", required=True)
    sync_full.add_argument("--config", type=Path, required=True)
    repair = commands.add_parser(
        "repair", help="按网页实际spec页请求补采清单内失败配置，保留原始证据"
    )
    repair.add_argument("--run", required=True)
    repair.set_defaults(concurrency=2, timeout=20)
    full_export = commands.add_parser(
        "export-full", help="校验完整清单并导出全部原文参数CSV，不受飞书列数限制"
    )
    full_export.add_argument("--run", required=True)
    export = commands.add_parser("export", help="校验快照，生成独立列CSV及字段审计")
    export.add_argument("--run", required=True)
    export.add_argument("--dictionary", type=Path)
    bootstrap_command = commands.add_parser("bootstrap", help="核验备份，旧两表改名并创建新三表")
    bootstrap_command.add_argument("--config", type=Path, required=True)
    bootstrap_command.add_argument("--backup", type=Path, required=True)
    schema_command = commands.add_parser(
        "schema-sync", help="仅向新表追加审计过的缺失列，不改旧列类型"
    )
    schema_command.add_argument("--config", type=Path, required=True)
    for name in ["plan", "apply", "verify"]:
        command = commands.add_parser(name, help=f"飞书试验表 {name}")
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--run", required=True)
    args = parser.parse_args()
    if args.command == "schema-sync":
        from .feishu import Client, sync_schema

        print(json.dumps(sync_schema(Client(args.config)), ensure_ascii=False))
        return
    if args.command == "bootstrap":
        from .feishu import Client
        from .migration import bootstrap

        print(json.dumps(bootstrap(Client(args.config), args.backup), ensure_ascii=False))
        return
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", args.run):
        parser.error("run must contain only letters, digits, underscores or hyphens")
    root = assert_external(args.data_root / args.run)
    if args.command in {"plan", "apply", "verify"}:
        from . import feishu

        result = getattr(feishu, args.command)(feishu.Client(args.config), root)
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.command == "prepare-full-sync":
        from .full_sync import prepare_batches

        print(json.dumps(prepare_batches(root), ensure_ascii=False))
        return
    if args.command == "sync-full":
        from .feishu import Client
        from .full_sync import sync_batches

        print(json.dumps(sync_batches(Client(args.config), root), ensure_ascii=False))
        return
    if args.command == "repair":
        from scrapy.crawler import CrawlerProcess

        from .repair import RepairSpider

        assert_run_allowed(root)
        settings = crawler_settings(root, args.concurrency, args.timeout)
        settings["LOG_FILE"] = str(root / "repair.log")
        process = CrawlerProcess(settings)
        process.crawl(RepairSpider, root=root)
        process.start()
        status = json.loads((root / "status.json").read_text())
        print(json.dumps({"phase": status["phase"], "missing_series": status["missing_series"]}))
        if status["phase"] != "complete":
            raise SystemExit(1)
        return
    if args.command == "export-full":
        from .full_export import export_full

        print(json.dumps(export_full(root), ensure_ascii=False))
        return
    if args.command == "export":
        print(json.dumps(export_run(root, args.dictionary), ensure_ascii=False))
        return
    catalog_rows = []
    if args.command == "crawl":
        if bool(args.brand) == bool(args.manifest):
            parser.error("Choose exactly one of --brand or --manifest")
        if args.manifest:
            if args.series or args.presale_series:
                parser.error(
                    "Manifest collection uses exactly its recorded series and sales status"
                )
            from .discovery import load_manifest

            manifest = load_manifest(args.manifest)
            catalog_rows = manifest["rows"]
    # A crawl never reuses a previous live run, including paused runs.
    assert_run_allowed(root)
    root.mkdir(parents=True, exist_ok=False)
    if args.command == "discover":
        from scrapy.crawler import CrawlerProcess

        from .discovery import DiscoverySpider

        process = CrawlerProcess(crawler_settings(root, args.concurrency, args.timeout))
        process.crawl(
            DiscoverySpider,
            root=root,
            expected_on_sale=args.expected_on_sale,
            expected_presale=args.expected_presale,
        )
        process.start()
        status = json.loads((root / "status.json").read_text())
        print(json.dumps(status, ensure_ascii=False))
        if status["phase"] != "complete":
            raise SystemExit(1)
        return
    if args.manifest:
        shutil.copytree(args.manifest.parent / "raw", root / "catalog/raw")
        save_json(root / "scope-manifest.json", manifest)
        catalog_rows = [
            {
                **row,
                "catalog_source": {
                    **row["catalog_source"],
                    "file": "catalog/" + row["catalog_source"]["file"],
                },
            }
            for row in catalog_rows
        ]
        for row in catalog_rows:
            if row.get("seriesimg", "").startswith("//"):
                row["seriesimg"] = "https:" + row["seriesimg"]
    save_json(
        root / "scope.json",
        {
            "manifest": str(args.manifest) if args.manifest else None,
            "brands": args.brand,
            "series_ids": args.series,
            "presale_series": args.presale_series,
            "concurrency": args.concurrency,
            "timeout": args.timeout,
        },
    )
    from scrapy.crawler import CrawlerProcess

    from .spider import CatalogSpider

    process = CrawlerProcess(crawler_settings(root, args.concurrency, args.timeout))
    process.crawl(
        CatalogSpider,
        root=root,
        brands=args.brand,
        series_ids=args.series,
        presale_series=args.presale_series,
        catalog_rows=catalog_rows,
    )
    process.start()
    status = json.loads((root / "status.json").read_text())
    print(
        json.dumps(
            {k: status[k] for k in ["phase", "completed_series", "missing_series", "errors"]},
            ensure_ascii=False,
        )
    )
    if status["phase"] != "complete":
        raise SystemExit(1)


def crawler_settings(root, concurrency, timeout):
    return {
        "ROBOTSTXT_OBEY": True,
        "USER_AGENT": "AutohomeVehicleCatalog/0.1 (+parameter research)",
        "CONCURRENT_REQUESTS": concurrency,
        "CONCURRENT_REQUESTS_PER_DOMAIN": concurrency,
        "DOWNLOAD_DELAY": 0.25,
        "RANDOMIZE_DOWNLOAD_DELAY": False,
        "DOWNLOAD_TIMEOUT": timeout,
        "RETRY_TIMES": 1,
        "RETRY_HTTP_CODES": [500, 502, 503, 504],
        "REDIRECT_ENABLED": False,
        "COOKIES_ENABLED": False,
        "TELNETCONSOLE_ENABLED": False,
        "LOG_FILE": str(root / "scrapy.log"),
        "LOG_LEVEL": "INFO",
        "DOWNLOADER_MIDDLEWARES": {"autohome_catalog.spider.EvidenceMiddleware": 800},
    }


if __name__ == "__main__":
    main()
