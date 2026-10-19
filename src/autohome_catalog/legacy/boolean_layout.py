"""Typed standard-equipment booleans, source option evidence, and migration plans."""

import hashlib
import json
import re
from collections import Counter

from ..export import TRIM_FIELDS
from ..parse import FAMILIES, cell_text
from ..storage import save_json
from .compact_display import compact

NEGATIVE = {"-", "—", "无", "不支持", "不配备"}
GENERIC = {"", "标配", "选配", "选装"}


def atom_status(value):
    if value == "●":
        return True
    if value == "○" or value in NEGATIVE:
        return False
    if value in (None, ""):
        return None
    raise ValueError(f"Unrecognized equipment status: {value!r}")


def parts(cell):
    """Preserve source subitem labels and prices; never split '/' inside a label."""
    result = []
    raw = cell.get("itemname")
    if raw not in (None, ""):
        if raw in {"●", "○"} | NEGATIVE:
            result.append(("", atom_status(raw), cell.get("priceinfo") or ""))
        elif re.fullmatch(r"(?:主|副|前|后)[●○—-](?:/(?:主|副|前|后)[●○—-])+", raw):
            result += [
                (label, atom_status(status), cell.get("priceinfo") or "")
                for label, status in re.findall(r"(主|副|前|后)([●○—-])", raw)
            ]
    for option in cell.get("sublist") or []:
        result.append(
            (
                option.get("name") or "",
                atom_status(option.get("value")),
                option.get("priceinfo") or cell.get("priceinfo") or "",
            )
        )
    return result


def short_label(source_name, label):
    label = compact(source_name, label + "标配（●）").removesuffix(" ●")
    return label.replace("车主服务（查找充电桩、加油站、停车场等）", "车主服务")


def load_cells(source, wanted):
    result = {}
    for path in sorted((source / "series").glob("*.json")):
        payload = json.loads(path.read_text()).get("payload", {})
        titles = {
            item["titleid"]: (
                group["groupname"],
                group["itemtype"],
                item["itemid"],
                item["itemname"],
            )
            for group in payload.get("titlelist", [])
            for item in group["items"]
        }
        for spec in payload.get("datalist", []):
            sid = str(spec["specid"])
            if sid in wanted:
                if sid in result:
                    raise ValueError("Duplicate source specification")
                result[sid] = {titles[c["titleid"]]: c for c in spec["paramconflist"]}
    if set(result) != wanted:
        raise ValueError("Source specification coverage mismatch")
    return result


def source_key(field, catalog):
    description = field.get("description") or ""
    hits = [
        tuple(f[k] for k in ("group", "section", "item_id", "name"))
        for f in catalog
        if description.startswith(f"汽车之家{f['group']}/{f['section']}；")
        and f"原始参数ID={f['item_id']}；" in description
        and f"原名={f['name']}。" in description
    ]
    if len(hits) > 1:
        raise ValueError("Ambiguous source field")
    return hits[0] if hits else None


def build(root, source):
    manifest = json.loads((root / "backup-verification.json").read_text())
    backup = {}
    for role, proof in manifest.items():
        path = root / "backup" / role / "records.json"
        if hashlib.sha256(path.read_bytes()).hexdigest() != proof["sha256"]:
            raise ValueError("Backup hash mismatch")
        backup[role] = json.loads(path.read_text())
    trims = {r["fields"]["来源配置ID"]: r for r in backup["trims"]}
    cells = load_cells(source, set(trims))
    catalog = json.loads((source / "full-field-catalog.json").read_text())
    schemas = {
        role: json.loads((root / "backup" / role / "schema.json").read_text())
        for role in ("trims", "equipment")
    }
    features, updates, hide, options, issues = [], {}, {}, [], []
    for role, schema in schemas.items():
        rows = {r["fields"]["来源配置ID"]: r for r in backup[role]}
        updates[role] = {}
        hide[role] = []
        views = json.loads((root / "backup" / role / "views.json").read_text())
        order = {name: index for index, name in enumerate(views[0]["visible"])}
        for field in sorted(schema, key=lambda f: order.get(f["name"], 10000)):
            if field["type"] not in {"text", "number"} or field["name"].startswith(
                ("原文", "来源")
            ):
                continue
            key = source_key(field, catalog)
            if key is None:
                continue
            source_name = key[3]
            by_spec = {sid: cells[sid].get(key) for sid in trims}
            values = [c for c in by_spec.values() if c is not None]
            extracted = [parts(c) for c in values]
            scalar = (
                field["type"] == "text"
                and any(extracted)
                and all(
                    all(label in GENERIC for label, status, price in p)
                    and c.get("itemname") in (None, "", "●", "○", *NEGATIVE)
                    for c, p in zip(values, extracted, strict=True)
                )
            )
            family = source_name in FAMILIES
            hybrid = (
                field["type"] == "text"
                and not scalar
                and not family
                and any(
                    c.get("itemname") in {"●", "○"}
                    or any(
                        (o.get("name") or "") in GENERIC and o.get("value") in {"●", "○"}
                        for o in c.get("sublist") or []
                    )
                    for c in values
                )
            )
            if not scalar and not family:
                for sid, row in rows.items():
                    old = row["fields"].get(field["name"])
                    if isinstance(old, str) and any(s in old for s in "●○"):
                        cleaned = re.sub(r"[●○]", "", old)
                        cleaned = "\n".join(line.strip() for line in cleaned.splitlines())
                        updates[role].setdefault(row["record_id"], {})[field["name"]] = cleaned
            else:
                hide[role].append(field["name"])
            labels = []
            if scalar or hybrid:
                labels = [""]
            elif family:
                for c, p in zip(values, extracted, strict=True):
                    for label, status, price in p:
                        if label not in NEGATIVE:
                            if (
                                label in GENERIC
                                and status is False
                                and c.get("itemname") in NEGATIVE
                            ):
                                continue
                            normalized = "" if label in GENERIC else label
                            if normalized not in labels:
                                labels.append(normalized)
            per_label = {label: {} for label in labels}
            for sid, row in rows.items():
                cell = by_spec.get(sid)
                if cell is None:
                    for label in labels:
                        per_label[label][sid] = None
                    continue
                p = parts(cell)
                if hybrid:
                    states = [status for label, status, price in p if type(status) is bool]
                    per_label[""][sid] = any(states) if states else None
                elif scalar or family:
                    result = {label: None for label in labels}
                    if cell.get("itemname") in NEGATIVE or any(
                        label in NEGATIVE and status is True for label, status, price in p
                    ):
                        result = {label: False for label in labels}
                    explicit = {}
                    for label, status, price in p:
                        label = "" if label in GENERIC else label
                        if label in NEGATIVE:
                            continue
                        if label == "" and label not in result and cell.get("itemname") in NEGATIVE:
                            continue
                        if label not in result:
                            issues.append(
                                [sid, field["name"], "Unmapped functional subitem", label]
                            )
                            continue
                        if label in explicit and explicit[label] != status:
                            issues.append([sid, field["name"], "Conflicting subitem status", label])
                        result[label] = status
                        explicit[label] = status
                    if (
                        "" in result
                        and result[""] is None
                        and any(type(status) is bool for label, status, price in p)
                    ):
                        result[""] = any(status is True for label, status, price in p)
                    if family and not p and cell.get("itemname") not in (None, "", *NEGATIVE):
                        issues.append(
                            [sid, field["name"], "Unparsed source text", cell.get("itemname")]
                        )
                    for label in labels:
                        per_label[label][sid] = result[label]
                for index, (label, status, price) in enumerate(p):
                    if status is False and (
                        any(
                            o.get("value") == "○" and (o.get("name") or "") == label
                            for o in cell.get("sublist") or []
                        )
                        or cell.get("itemname") == "○"
                        or re.search(re.escape(label) + "○", cell.get("itemname") or "")
                    ):
                        identity = json.dumps([sid, key, index, label], ensure_ascii=False)
                        options.append(
                            {
                                "明细ID": "BOOL:"
                                + hashlib.sha256(identity.encode()).hexdigest()[:24],
                                "来源配置ID": sid,
                                "来源项目ID": str(key[2]),
                                "项目名称": field["name"],
                                "选装项目": label or field["name"],
                                "是否标配": False,
                                "选装价格": price or None,
                                "原始条件": cell_text(cell),
                            }
                        )
            for label in labels:
                pretty = short_label(source_name, label)
                prefix = FAMILIES.get(source_name, "")
                name = (
                    field["name"]
                    if label == ""
                    else pretty
                    if prefix in pretty
                    else prefix + pretty
                )
                features.append(
                    {
                        "name": name + "标配" if hybrid else name,
                        "preserve_source": hybrid,
                        "source_key": list(key),
                        "source_field": field["name"],
                        "label": label,
                        "source_role": role,
                        "values": per_label[label],
                    }
                )
    name_counts = Counter(f["name"] for f in features)
    for f in features:
        if name_counts[f["name"]] > 1:
            f["name"] += (
                "·"
                + hashlib.sha256(
                    json.dumps([f["source_key"], f["label"]], ensure_ascii=False).encode()
                ).hexdigest()[:4]
            )
    assert len({f["name"] for f in features}) == len(features)
    result = {
        "features": features,
        "updates": updates,
        "hide": hide,
        "options": options,
        "issues": issues,
        "summary": {
            "configurations": len(trims),
            "boolean_columns": len(features),
            "option_rows": len(options),
            "issues": len(issues),
            "true": sum(v is True for f in features for v in f["values"].values()),
            "false": sum(v is False for f in features for v in f["values"].values()),
            "unknown": sum(v is None for f in features for v in f["values"].values()),
        },
    }
    save_json(root / "boolean-plan.json", result)
    return result


def clean_text(value):
    if not isinstance(value, str):
        return value
    return re.sub(r"(?:标配|选配|选装)*[（(]?[●○][）)]?", "", value).strip()


def compile_tables(root, plan, *, output=None):
    """Replace each original parameter slot with its typed children, in source order."""
    if plan["issues"]:
        raise ValueError("Unresolved source interpretation issues; no writes")
    backup = {
        r: json.loads((root / "backup" / r / "records.json").read_text())
        for r in ("trims", "equipment")
    }
    rows = {r: {x["fields"]["来源配置ID"]: x for x in data} for r, data in backup.items()}
    schemas = {r: json.loads((root / "backup" / r / "schema.json").read_text()) for r in backup}
    identity = ["配置名称", "model_id", "来源配置ID", "车系名称", "品牌"]
    metadata = set(TRIM_FIELDS + identity)
    schema_map = {f["name"]: f for f in schemas["trims"]}

    def definition(f):
        return {k: v for k, v in f.items() if k != "id"}

    first = [definition(schema_map[n]) for n in identity]
    first += [
        definition(f)
        for f in schemas["trims"]
        if f["name"] in metadata and f["name"] not in identity
    ]
    feature_map = {}
    for f in plan["features"]:
        if f.get("preserve_source"):
            continue
        feature_map.setdefault((f["source_role"], f["source_field"]), []).append(f)
    slots = []
    for role in ("trims", "equipment"):
        visible = json.loads((root / "backup" / role / "views.json").read_text())[0]["visible"]
        order = {n: i for i, n in enumerate(visible)}
        for f in sorted(schemas[role], key=lambda x: order.get(x["name"], 10000)):
            if f["type"] == "link" or f["name"] in metadata:
                continue
            children = feature_map.get((role, f["name"]), [])
            if children:
                block = []
                for child in children:
                    d = {
                        "name": child["name"],
                        "type": "checkbox",
                        "description": f"是否标配：true=标配，false=选装或明确未配，null=未披露。汽车之家{child['source_key']}；子项={child['label'] or '整体'}。选装条件见选装明细。",
                    }
                    block.append((d, child["values"]))
                slots.append((role, f["name"], block))
            else:
                values = {
                    sid: plan["updates"][role]
                    .get(row["record_id"], {})
                    .get(f["name"], row["fields"].get(f["name"]))
                    for sid, row in rows[role].items()
                }
                if not f["name"].startswith("原文") and f["name"] != "参数原文存档":
                    values = {k: clean_text(v) for k, v in values.items()}
                slots.append((role, f["name"], [(definition(f), values)]))
    packets = []
    definitions = first[:]
    data = {
        sid: {f["name"]: row["fields"].get(f["name"]) for f in first}
        for sid, row in rows["trims"].items()
    }
    mapping = []
    used = set(f["name"] for f in first)
    for role, original, block in slots:
        if len(definitions) + len(block) > 285:
            packets.append({"schema": definitions, "data": data})
            definitions = [definition(schema_map[n]) for n in identity]
            data = {
                sid: {n: row["fields"].get(n) for n in identity}
                for sid, row in rows["trims"].items()
            }
        destinations = []
        for f, values in block:
            f = dict(f)
            if f["name"] in used:
                f["name"] += (
                    "·" + hashlib.sha256(f"{role}/{original}/{f['name']}".encode()).hexdigest()[:4]
                )
            used.add(f["name"])
            definitions.append(f)
            for sid, value in values.items():
                data[sid][f["name"]] = value
            destinations.append(f["name"])
        mapping.append(
            {
                "source_role": role,
                "source_field": original,
                "table_index": len(packets),
                "fields": destinations,
            }
        )
    packets.append({"schema": definitions, "data": data})
    for i, p in enumerate(packets):
        p["name"] = "vehicle_trims" if i == 0 else "配置参数扩展" + (str(i) if i > 1 else "")
        p["pending_name"] = p["name"] + "（布尔迁移中）"
    # Hybrid specifications retain their text column and gain an adjacent status.
    # Add after partitioning to keep established parameter-table boundaries stable.
    for child in plan["features"]:
        if not child.get("preserve_source"):
            continue
        match = next(
            m
            for m in mapping
            if (m["source_role"], m["source_field"])
            == (child["source_role"], child["source_field"])
        )
        packet = packets[match["table_index"]]
        definition = {
            "name": child["name"],
            "type": "checkbox",
            "description": "是否标配：true=标配，false=选装或明确未配，null=未披露。规格保留在相邻原字段。",
        }
        at = next(i for i, f in enumerate(packet["schema"]) if f["name"] == match["fields"][0])
        packet["schema"].insert(at, definition)
        match["fields"].insert(0, child["name"])
        for sid, value in child["values"].items():
            packet["data"][sid][child["name"]] = value
        if len(packet["schema"]) > 292:
            raise ValueError("Insufficient column headroom for hybrid status and relations")
    result = {
        "packets": packets,
        "mapping": mapping,
        "summary": {
            "tables": len(packets),
            "parameter_columns": [len(p["schema"]) for p in packets],
            "rows_per_table": len(rows["trims"]),
            "source_slots": len(mapping),
            "boolean_columns": len(plan["features"]),
            "optional_rows": len(plan["options"]),
        },
    }
    save_json(output or root / "typed-tables.json", result)
    return result


def compile_details(root, source, plan):
    original = json.loads((root / "backup" / "special" / "records.json").read_text())
    trims = {
        r["fields"]["来源配置ID"]: r["fields"]
        for r in json.loads((root / "backup/trims/records.json").read_text())
    }
    cells = load_cells(source, set(trims))
    schema = json.loads((root / "backup/special/schema.json").read_text())
    fields = [{k: v for k, v in f.items() if k != "id"} for f in schema if f["type"] != "link"]
    fields.sort(key=lambda f: f["name"] != "项目名称")
    category = next(f for f in fields if f["name"] == "类别")
    category["options"].append({"name": "选装参数"})
    fields += [
        {
            "name": "是否标配",
            "type": "checkbox",
            "description": "true=标配，false=选装或明确未配，null=未披露。价格独立保留。",
        }
    ]
    fields += [{"name": n, "type": "text"} for n in ("选装项目", "选装价格", "条件说明")]
    desired = {}
    for row in original:
        f = dict(row["fields"])
        f.pop("对应配置")
        matches = [
            c
            for k, c in cells[f["来源配置ID"]].items()
            if k[0] == "个性化" and str(k[2]) == f["来源项目ID"]
        ]
        if len(matches) != 1:
            raise ValueError("Special item source identity mismatch")
        extracted = parts(matches[0])
        states = {status for label, status, price in extracted if type(status) is bool}
        if len(states) != 1:
            raise ValueError("Special item status ambiguous")
        f["是否标配"] = next(iter(states))
        f["选装项目"] = f["项目名称"]
        f["选装价格"] = (
            "；".join(dict.fromkeys(price for label, status, price in extracted if price)) or None
        )
        f["内容"] = clean_text(f["内容"])
        f["条件说明"] = None
        desired[f["明细ID"]] = f
    for index, option in enumerate(plan["options"]):
        source_fields = trims[option["来源配置ID"]]
        f = {field["name"]: source_fields.get(field["name"]) for field in fields}
        f.update({k: v for k, v in option.items() if k != "原始条件"})
        f["类别"] = ["选装参数"]
        f["内容"] = option["选装项目"]
        f["条件说明"] = clean_text(option["原始条件"])
        f["参数顺序"] = index
        if f["明细ID"] in desired:
            raise ValueError("Duplicate option identity")
        desired[f["明细ID"]] = f
    result = {
        "schema": fields,
        "data": desired,
        "name": "专属配置明细",
        "pending_name": "专属配置明细（布尔迁移中）",
    }
    save_json(root / "typed-details.json", result)
    return result


def partition_details(packet, limit=18000):
    """Keep all configurations of a model together, below the observed table row quota."""
    groups = {}
    for key, fields in packet["data"].items():
        groups.setdefault(fields["model_id"], {})[key] = fields
    partitions = []
    current = {}
    for group in groups.values():
        if len(group) > limit:
            raise ValueError("A model's detail group exceeds the row quota")
        if current and len(current) + len(group) > limit:
            partitions.append(current)
            current = {}
        current.update(group)
    if current:
        partitions.append(current)
    result = []
    for index, data in enumerate(partitions):
        name = "专属配置明细" + (str(index + 1) if index else "")
        result.append(
            {**packet, "name": name, "pending_name": name + "（布尔迁移中）", "data": data}
        )
    return result
