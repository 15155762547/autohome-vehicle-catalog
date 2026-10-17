"""Source identity, scope, and cell interpretation; no requests or Feishu writes."""

import json
import re
from decimal import Decimal

from .models import CatalogSeries, Cell, ParameterResponse

FieldKey = tuple[str, str, int, str]


def field_key(group, item) -> FieldKey:
    return group.groupname, group.itemtype, item.itemid, item.itemname


def parse_parameters(payload: dict, series_id: int, spec_ids: set[int]):
    parsed = ParameterResponse.model_validate(payload)
    if parsed.bread.seriesid != series_id:
        raise ValueError(f"Wrong series: {parsed.bread.seriesid}, expected {series_id}")
    by_id = {spec.specid: spec for spec in parsed.datalist}
    if len(by_id) != len(parsed.datalist):
        raise ValueError("Duplicate spec IDs")
    if missing := spec_ids - by_id.keys():
        raise ValueError(f"Missing requested specs: {sorted(missing)}")
    titles = {}
    for group in parsed.titlelist:
        for item in group.items:
            key = field_key(group, item)
            if item.titleid in titles and titles[item.titleid] != key:
                raise ValueError(f"Conflicting titleid: {item.titleid}")
            titles[item.titleid] = key
    result = {}
    for spec_id in sorted(spec_ids):
        cells = {}
        for cell in by_id[spec_id].paramconflist:
            if cell.titleid not in titles:
                raise ValueError(f"Unmapped titleid: {spec_id}/{cell.titleid}")
            key = titles[cell.titleid]
            if key in cells and cells[key] != cell:
                raise ValueError(f"Conflicting cells: {spec_id}/{key}")
            cells[key] = cell
        result[spec_id] = cells
    return result


def cell_text(value: dict | Cell) -> str:
    cell = value.model_dump() if isinstance(value, Cell) else value
    chunks = []
    if cell.get("itemname") not in (None, ""):
        chunks.append(cell["itemname"])
    for option in cell.get("sublist") or []:
        text = str(option.get("name", "")) + str(option.get("value", ""))
        if option.get("priceinfo"):
            text += f"（{option['priceinfo']}）"
        chunks.append(text)
    if cell.get("priceinfo"):
        chunks.append("价格条件：" + cell["priceinfo"])
    if cell.get("colorinfo"):
        chunks.append("颜色：" + json.dumps(cell["colorinfo"], ensure_ascii=False))
    return "；".join(chunks)


def render_cell(value: dict | Cell) -> str:
    return cell_text(value).replace("●", "标配（●）").replace("○", "选配（○）")


def numeric_cell(value: dict | Cell) -> int | float | None:
    cell = value.model_dump() if isinstance(value, Cell) else value
    raw = cell.get("itemname")
    if raw not in (None, "") and cell.get("sublist"):
        return None
    if cell.get("colorinfo"):
        return None
    if raw in (None, ""):
        options = cell.get("sublist") or []
        if len(options) != 1 or options[0].get("value") != "●":
            return None
        if options[0].get("priceinfo"):
            return None
        raw = options[0].get("name")
    if cell.get("priceinfo") or not re.fullmatch(r"-?\d+(?:\.\d+)?", str(raw)):
        return None
    number = Decimal(raw)
    return int(number) if number == number.to_integral_value() else float(number)


def choose_series(rows: list[dict], presale_series: set[int], on_sale_states=None):
    on_sale_states = {20, 30} if on_sale_states is None else set(on_sale_states)
    chosen, review = [], []
    seen = set()
    for row in rows:
        series = CatalogSeries.model_validate(row)
        if series.seriesid in seen:
            raise ValueError(f"Duplicate series ID: {series.seriesid}")
        seen.add(series.seriesid)
        if series.newenergy != 1:
            continue
        if series.seriesstate in on_sale_states:
            chosen.append({**row, "scope_status": "在售", "scope_basis": "源站在售目录"})
        elif series.seriesstate == 10 and series.seriesid in presale_series:
            chosen.append(
                {**row, "scope_status": "预售", "scope_basis": "历史显式样本；正式预售范围以找新能源预售目录核验"}
            )
        elif series.seriesstate in {0, 10}:
            review.append({**row, "review_reason": "未售，缺少明确预售证据"})
    return chosen, review
