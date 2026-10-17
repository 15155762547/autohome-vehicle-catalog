"""Explicit, bounded commands. Network collection and writes are separate."""

import argparse
import json
import os
import re
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
    crawl = commands.add_parser("crawl", help="仅采指定品牌，可进一步限定车系")
    crawl.add_argument("--brand", type=int, action="append", required=True)
    crawl.add_argument("--series", type=int, action="append", default=[])
    crawl.add_argument("--presale-series", type=int, action="append", default=[])
    crawl.add_argument("--run", required=True)
    crawl.add_argument("--concurrency", type=int, choices=[1, 2, 4], default=2)
    crawl.add_argument("--timeout", type=int, choices=[15, 20, 30], default=20)
    export = commands.add_parser("export", help="校验快照，生成独立列CSV及字段审计")
    export.add_argument("--run", required=True)
    export.add_argument("--dictionary", type=Path)
    bootstrap_command = commands.add_parser("bootstrap", help="核验备份，旧两表改名并创建新三表")
    bootstrap_command.add_argument("--config", type=Path, required=True)
    bootstrap_command.add_argument("--backup", type=Path, required=True)
    for name in ["plan", "apply", "verify"]:
        command = commands.add_parser(name, help=f"飞书试验表 {name}")
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--run", required=True)
    args = parser.parse_args()
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
    if args.command == "export":
        print(json.dumps(export_run(root, args.dictionary), ensure_ascii=False))
        return
    # A crawl never reuses a previous live run, including paused runs.
    assert_run_allowed(root)
    root.mkdir(parents=True, exist_ok=False)
    save_json(
        root / "scope.json",
        {
            "brands": args.brand,
            "series_ids": args.series,
            "presale_series": args.presale_series,
            "concurrency": args.concurrency,
            "timeout": args.timeout,
        },
    )
    from scrapy.crawler import CrawlerProcess

    from .spider import CatalogSpider

    process = CrawlerProcess(
        settings={
            "ROBOTSTXT_OBEY": True,
            "USER_AGENT": "AutohomeVehicleCatalog/0.1 (+parameter research)",
            "CONCURRENT_REQUESTS": args.concurrency,
            "CONCURRENT_REQUESTS_PER_DOMAIN": args.concurrency,
            "DOWNLOAD_DELAY": 0.25,
            "RANDOMIZE_DOWNLOAD_DELAY": False,
            "DOWNLOAD_TIMEOUT": args.timeout,
            "RETRY_TIMES": 1,
            "RETRY_HTTP_CODES": [500, 502, 503, 504],
            "REDIRECT_ENABLED": False,
            "COOKIES_ENABLED": False,
            "TELNETCONSOLE_ENABLED": False,
            "LOG_FILE": str(root / "scrapy.log"),
            "LOG_LEVEL": "INFO",
            "DOWNLOADER_MIDDLEWARES": {"autohome_catalog.spider.EvidenceMiddleware": 800},
        }
    )
    process.crawl(
        CatalogSpider,
        root=root,
        brands=args.brand,
        series_ids=args.series,
        presale_series=args.presale_series,
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


if __name__ == "__main__":
    main()
