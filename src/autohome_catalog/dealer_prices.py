"""Collect city-specific dealer quotes and update only the existing quote column."""

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from pydantic import BaseModel, ConfigDict, Field

from .feishu import Client, record_index
from .storage import assert_external, assert_run_allowed, guard_response, save_json

QUOTE = "经销商报价"
COLUMNS = ["来源配置ID", "配置名称", "model_id", QUOTE]
CITY = 110100  # Beijing: the same explicit location as the accepted single-spec probe.
ENDPOINT = "https://www.autohome.com.cn/web-main/car/spec/getspecinfo"


class AskPrice(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    newsprice: float = Field(ge=0, strict=True)


class QuoteItem(BaseModel):
    specid: int = Field(strict=True)
    askprice: AskPrice


def parse_quotes(payload, wanted):
    if payload.get("returncode") != 0:
        raise ValueError("Dealer source returned an unsuccessful envelope")
    items = [QuoteItem.model_validate(x) for x in payload["result"]["list"]]
    ids = [x.specid for x in items]
    if len(set(ids)) != len(ids) or set(ids) != set(wanted):
        raise ValueError("Missing, duplicate or extra configuration IDs in quote response")
    return {
        str(item.specid): {
            "price_yuan": item.askprice.newsprice,
            "display": (
                format((Decimal(str(item.askprice.newsprice)) / 10000).normalize(), "f") + "万起"
                if item.askprice.newsprice > 0
                else "暂无报价"
            ),
        }
        for item in items
    }


def guard(config, command, args):
    if command in {"+record-list", "+field-list"}:
        return
    if command == "+record-batch-update" and args == ["--table-id", config["tables"]["trims"]]:
        return
    raise ValueError(f"Outside dealer quote update: {command}")


def records(client):
    rows, offset, revision = [], 0, None
    while True:
        args = [
            "--table-id",
            client.config["tables"]["trims"],
            "--limit",
            "200",
            "--offset",
            str(offset),
        ]
        for name in COLUMNS:
            args += ["--field-id", name]
        data = client.call("+record-list", args)
        if revision is not None and revision != data["rev"]:
            raise ValueError("Table changed during dealer quote export")
        revision = data["rev"]
        rows.extend(
            {"record_id": rid, "fields": dict(zip(data["fields"], values, strict=True))}
            for rid, values in zip(data["record_id_list"], data["data"], strict=True)
        )
        if not data["has_more"]:
            return record_index(rows, "来源配置ID")
        if not data["data"]:
            raise ValueError("Empty quote export page")
        offset += len(data["data"])


def fetch(root, ids, index):
    assert_run_allowed(root)
    url = (
        ENDPOINT + "?" + urlencode({"type": 1, "specids": ",".join(map(str, ids)), "cityid": CITY})
    )
    for attempt in range(2):
        try:
            try:
                response = urlopen(url, timeout=30)
            except HTTPError as error:
                response = error
            with response:
                raw, status = response.read(), response.status
                content_type = response.headers.get("Content-Type", "")
            break
        except (URLError, TimeoutError):
            if attempt:
                raise
            time.sleep(2)
    raw_path = root / "responses" / f"{index:04d}.json"
    raw_path.parent.mkdir(exist_ok=True)
    raw_path.write_bytes(raw)
    receipt = {
        "url": url,
        "status": status,
        "cityid": CITY,
        "spec_ids": ids,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "file": str(raw_path.relative_to(root)),
    }
    save_json(root / "sources" / f"{index:04d}.json", receipt)
    try:
        guard_response(status, raw, content_type)
    except ValueError as error:
        if status in (401, 403, 429) or "challenge" in str(error):
            save_json(root / "PAUSED.json", {"reason": str(error), "source": receipt})
        raise
    return parse_quotes(json.loads(raw), ids)


def run(config, root, apply=False):
    root = assert_external(root)
    root.mkdir(parents=True, exist_ok=True)
    client = Client(config)
    client.config["_dealer_prices"] = True
    tid = client.config["tables"]["trims"]
    schema = client.call("+field-list", ["--table-id", tid])["fields"]
    if next(f for f in schema if f["name"] == QUOTE)["type"] != "text":
        raise ValueError("Dealer quote column must remain text")
    backup_path = root / "records-before.json"
    if not backup_path.exists():
        save_json(root / "schema-before.json", schema)
        save_json(backup_path, list(records(client).values()))
    before = record_index(json.loads(backup_path.read_text()), "来源配置ID")
    ids = sorted(map(int, before))
    quotes, failures = {}, {}
    for start in range(0, len(ids), 50):
        batch, index = ids[start : start + 50], start // 50
        cache = root / "parsed" / f"{index:04d}.json"
        try:
            if cache.exists():
                saved = json.loads(cache.read_text())
                receipt = json.loads((root / "sources" / f"{index:04d}.json").read_text())
                raw = root / receipt["file"]
                if (
                    receipt["cityid"] != CITY
                    or receipt["spec_ids"] != batch
                    or hashlib.sha256(raw.read_bytes()).hexdigest() != receipt["sha256"]
                ):
                    raise ValueError("Quote cache identity or hash mismatch")
                if saved != parse_quotes(json.loads(raw.read_bytes()), batch):
                    raise ValueError("Quote cache differs from source")
            else:
                time.sleep(0.5)
                saved = fetch(root, batch, index)
                save_json(cache, saved)
            quotes.update(saved)
        except Exception as error:
            failures.update({str(sid): str(error) for sid in batch})
            if (root / "PAUSED.json").exists():
                break
        save_json(
            root / "progress.json",
            {"total": len(ids), "collected": len(quotes), "failed": len(failures)},
        )
        print(
            f"Quotes {min(start + 50, len(ids))}/{len(ids)}; success={len(quotes)}, failed={len(failures)}",
            flush=True,
        )
    save_json(root / "quotes.json", quotes)
    save_json(root / "failures.json", failures)
    if not apply:
        return {"collected": len(quotes), "failed": len(failures)}
    current = records(client)
    if current.keys() != before.keys():
        raise ValueError("Configuration set changed since backup")
    updates = {}
    for sid, row in current.items():
        old = before[sid]
        if row["record_id"] != old["record_id"] or any(
            row["fields"].get(k) != old["fields"].get(k) for k in COLUMNS if k != QUOTE
        ):
            raise ValueError("Configuration identity changed since backup")
        if sid not in quotes:
            continue
        value = quotes[sid]["display"]
        if row["fields"].get(QUOTE) not in (old["fields"].get(QUOTE), value):
            raise ValueError("Quote was manually changed since backup")
        if row["fields"].get(QUOTE) != value:
            updates[row["record_id"]] = {QUOTE: value}
    save_json(root / "write-plan.json", updates)
    items = list(updates.items())
    for start in range(0, len(items), 200):
        patch = dict(items[start : start + 200])
        if any(set(fields) != {QUOTE} for fields in patch.values()):
            raise ValueError("Only the dealer quote column may be written")
        client.call("+record-batch-update", ["--table-id", tid], {"update_records": patch})
        time.sleep(0.5)
        print(f"Written {min(start + 200, len(items))}/{len(items)}", flush=True)
    after = records(client)
    if after.keys() != before.keys():
        raise ValueError("Configuration count changed after quote write")
    for sid, old in before.items():
        expected = (
            {**old, "fields": {**old["fields"], QUOTE: quotes[sid]["display"]}}
            if sid in quotes
            else old
        )
        if after[sid] != expected:
            raise ValueError(f"Dealer quote read-back mismatch: {sid}")
    actual_schema = client.call("+field-list", ["--table-id", tid])["fields"]
    if {f["id"]: f for f in actual_schema} != {f["id"]: f for f in schema}:
        raise ValueError("Schema changed during quote collection")
    save_json(root / "records-after.json", list(after.values()))
    result = {
        "total": len(ids),
        "with_quote": sum(q["price_yuan"] > 0 for q in quotes.values()),
        "no_quote": sum(q["price_yuan"] == 0 for q in quotes.values()),
        "failed": len(failures),
        "unprocessed": len(set(before) - quotes.keys() - failures.keys()),
        "updated": len(updates),
        "verified": len(after),
        "cityid": CITY,
        "city": "北京",
    }
    save_json(root / "verification.json", result)
    labels = {
        "total": "配置总数",
        "with_quote": "有报价",
        "no_quote": "暂无报价",
        "failed": "采集失败",
        "unprocessed": "未处理",
        "updated": "本轮更新",
        "verified": "回读通过",
        "cityid": "地区ID",
        "city": "地区",
    }
    (root / "经销商报价交付说明.md").write_text(
        "# 经销商报价补采\n\n"
        + "\n".join(f"- {labels[k]}：{v}" for k, v in result.items())
        + "\n\n统一北京地区，与单配置试采一致；价格是经销商起报价，不代表最终成交价。正常响应newsprice=0填暂无报价，失败及未处理保留原值。只更新经销商报价列；回读全部配置ID、记录ID、配置名称、model_id及报价，字段定义保持原样。原始响应、时间、地区及SHA256均在本目录保留。\n"
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.config, args.root, args.apply), ensure_ascii=False))
