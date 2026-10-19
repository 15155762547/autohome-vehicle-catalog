"""Source-preserving color items, stable identities and conservative base-color labels."""

import colorsys
import hashlib
import json
import re
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

BASE_COLORS = list("黑白灰银红蓝绿黄橙棕米金紫粉")
RIGHT = ["品牌", "车系名称", "配置名称", "来源配置ID"]
KEY = "颜色明细ID"
AVAILABILITY = ["外观、内饰都有资料", "仅外观", "仅内饰", "未提供有效颜色资料"]
GALLERY = re.compile(r"https://car\.autohome\.com\.cn/pic/spec-(\d+)-(I?\d+)\.html")


class SourceColor(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    name: str = Field(min_length=1)
    value: str
    price: int = Field(ge=0)
    picnum: int = Field(ge=0)
    linkurl: str | None

    @field_validator("value")
    @classmethod
    def color_values(cls, value):
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}(?:/#[0-9A-Fa-f]{6})?", value):
            raise ValueError("Unexpected ordered color values")
        return value


class ColorList(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal[1, 2]
    list: list[SourceColor]


def source_list(text, side):
    if text in (None, "", "-", "—"):
        return []
    raw = text.removeprefix("颜色：")
    for _ in range(3):
        try:
            decoded = json.loads(raw)
            break
        except json.JSONDecodeError:
            raw = raw.replace('\\"', '"')
    else:
        raise ValueError("Malformed source color JSON")
    source = ColorList.model_validate(decoded)
    if source.type != (1 if side == "外观" else 2):
        raise ValueError("Source color side differs")
    return source.list


def gallery_identity(url, spec, side):
    if not url:
        return None
    match = GALLERY.fullmatch(url)
    if not match or match[1] != str(spec) or match[2].startswith("I") != (side == "内饰"):
        raise ValueError(f"Gallery identity differs: {spec}/{side}/{url}")
    return match[2]


def stable_id(spec, side, item):
    source_id = gallery_identity(item.linkurl, spec, side)
    if source_id is None:
        payload = json.dumps([item.name, item.value], ensure_ascii=False, separators=(",", ":"))
        source_id = "h" + hashlib.sha256(payload.encode()).hexdigest()[:24]
    return f"{spec}:{side}:{source_id}"


def hex_families(value):
    red, green, blue = (int(value[i : i + 2], 16) / 255 for i in (1, 3, 5))
    hue, sat, light = colorsys.rgb_to_hsv(red, green, blue)
    if sat < 0.22:
        # Paint names are marketing names; silver cannot be inferred from RGB alone.
        return set("黑灰银白米") if light < 0.35 else set("灰银白米")
    hue *= 360
    if hue < 20 or hue >= 345:
        return set("红粉棕")
    if hue < 65:
        return set("橙黄棕米金")
    if hue < 165:
        return set("绿黄金")
    if hue < 260:
        return set("蓝绿")
    return set("紫粉红")


def classify(name, value):
    parts, colors = re.split(r"[/／]", name), value.split("/")
    if len(parts) != len(colors):
        return [], "待核对"
    labels = []
    english = {
        "black": "黑",
        "white": "白",
        "grey": "灰",
        "gray": "灰",
        "silver": "银",
        "red": "红",
        "blue": "蓝",
        "green": "绿",
        "yellow": "黄",
        "orange": "橙",
        "brown": "棕",
        "beige": "米",
        "gold": "金",
        "purple": "紫",
        "pink": "粉",
    }
    for part, color in zip(parts, colors, strict=True):
        found = {c for c in BASE_COLORS if c in part}
        found.update(v for k, v in english.items() if re.search(rf"\b{k}\b", part, re.I))
        if len(found) != 1 or not found <= hex_families(color):
            return [], "待核对"
        labels.extend(c for c in BASE_COLORS if c in found and c not in labels)
    return labels, "名称与色值相符"


def compile_colors(source, index):
    if source.keys() != index.keys():
        raise ValueError("Color source/configuration key sets differ")
    output, statuses = {}, {}
    counts = Counter()
    for spec, row in source.items():
        fields, parent = row["fields"], index[spec]
        pf = parent["fields"]
        for name in ["车型全称", "model_id", *RIGHT]:
            if fields.get(name) != pf.get(name):
                raise ValueError(f"Parent identity differs: {spec}/{name}")
        if fields.get("对应配置") != [{"id": parent["record_id"]}]:
            raise ValueError(f"Old color association differs: {spec}")
        raw = json.loads(fields["颜色原文JSON"]) if fields.get("颜色原文JSON") else {}
        sides = {}
        for side in ["外观", "内饰"]:
            column = side + "颜色"
            backup = source_list(raw.get(column), side)
            display = source_list(fields.get(column), side)
            if backup != display:
                raise ValueError(f"Display/raw backup differs: {spec}/{side}")
            sides[side] = backup
            duplicates = Counter((c.name, c.value) for c in backup)
            names = Counter(c.name for c in backup)
            for order, item in enumerate(backup, 1):
                ident = stable_id(spec, side, item)
                if ident in output:
                    raise ValueError(f"Stable color identity collision: {ident}")
                labels, classification = classify(item.name, item.value)
                source_id = gallery_identity(item.linkurl, spec, side)
                values = {
                    "颜色项目": f"{side}｜{item.name}",
                    "车型全称": pf["车型全称"],
                    "model_id": pf["model_id"],
                    "部位": [side],
                    "基础色": labels,
                    "单色／双拼": ["双拼" if "/" in item.value else "单色"],
                    "加价（元）": item.price or None,
                    "价格说明": ["已列加价" if item.price else "未列加价"],
                    "图库入口": item.linkurl,
                    "图库核验状态": ["结构及身份匹配（未实测）" if source_id else "源数据未提供"],
                    "分类状态": [classification],
                    "对应配置": [{"id": parent["record_id"]}],
                    "备注": (
                        "同名不同图库项目"
                        if duplicates[item.name, item.value] > 1
                        else "同名不同色值项目"
                        if names[item.name] > 1
                        else None
                    ),
                    "原厂色名": item.name,
                    "完整色值": item.value,
                    "源颜色编号": source_id,
                    KEY: ident,
                    "原始顺序": order,
                    "单项原文JSON": json.dumps(item.model_dump(), ensure_ascii=False),
                    "来源采集时间": pf.get("来源采集时间"),
                    **{name: pf[name] for name in RIGHT},
                }
                output[ident] = values
                counts[side] += 1
                counts[classification] += 1
                counts["有图库" if source_id else "无图库"] += 1
        status = (
            AVAILABILITY[0]
            if sides["外观"] and sides["内饰"]
            else AVAILABILITY[1]
            if sides["外观"]
            else AVAILABILITY[2]
            if sides["内饰"]
            else AVAILABILITY[3]
        )
        statuses[spec] = status
    counts.update({k: list(statuses.values()).count(k) for k in AVAILABILITY})
    return output, statuses, dict(counts)
