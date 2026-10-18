import pytest

from autohome_catalog.boolean_layout import (
    atom_status,
    clean_text,
    partition_details,
    parts,
    short_label,
)
from autohome_catalog.boolean_migration import guard, verify_fields


def test_standard_optional_absent_and_unknown_are_distinct():
    assert atom_status("●") is True
    assert atom_status("○") is False
    assert atom_status("-") is False
    assert atom_status(None) is None
    with pytest.raises(ValueError):
        atom_status("option")


def test_front_rear_and_optional_price_survive():
    assert parts({"itemname": "前●/后○", "priceinfo": "4600元"}) == [
        ("前", True, "4600元"),
        ("后", False, "4600元"),
    ]
    assert parts(
        {
            "itemname": "",
            "sublist": [
                {"name": "标准/舒适", "value": "●"},
                {"name": "腰部支撑(4向)", "value": "○", "priceinfo": "1000元"},
            ],
        }
    ) == [("标准/舒适", True, ""), ("腰部支撑(4向)", False, "1000元")]


def test_short_label_keeps_seat_directions_and_position():
    assert short_label("主座椅调节方式", "高低调节(4向)") == "高低(4向)"
    assert short_label("前排座椅功能", "按摩(仅副驾驶位)") == "按摩(仅副驾)"


def test_text_content_price_and_numbers_survive_removing_state_markers():
    assert (
        clean_text("包括：辅助驾驶。；选配选配（○）（32000元）") == "包括：辅助驾驶。；（32000元）"
    )
    assert clean_text(123) == 123


def test_verify_rejects_boolean_strings_numbers_and_unknown_coercion():
    verify_fields({"功能": True, "未披露": None}, {"功能": True, "未披露": None})
    for expected, actual in [(True, "true"), (False, "false"), (False, 0), (None, False)]:
        with pytest.raises(ValueError):
            verify_fields({"状态": expected}, {"状态": actual})


def test_guard_cannot_change_original_parameter_rows():
    c = {
        "tables": {"models": "model", "trims": "original"},
        "_boolean_migration": {
            "new_tables": ["new"],
            "pending_names": ["pending"],
            "publish_names": {},
            "archive_names": {},
            "folder": "folder",
        },
    }
    guard(c, "+record-batch-create", ["--table-id", "new"])
    with pytest.raises(ValueError):
        guard(c, "+record-batch-update", ["--table-id", "original"])
    with pytest.raises(ValueError):
        guard(c, "+field-delete", ["--table-id", "new"])


def test_create_timeout_only_replays_missing_stable_keys(monkeypatch):
    import autohome_catalog.boolean_migration as migration

    class Client:
        last_failure = {"code": 800100001}

        def __init__(self):
            self.calls = []

        def call(self, command, args, body):
            self.calls.append(body["create_records"])
            if len(self.calls) == 1:
                raise RuntimeError("reply lost after one record committed")

    monkeypatch.setattr(migration.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        migration,
        "read_records",
        lambda *args: [
            {"record_id": "saved", "fields": {"来源配置ID": "1", "功能": True}},
        ],
    )
    client = Client()
    batch = [{"来源配置ID": "1", "功能": True}, {"来源配置ID": "2", "功能": False}]
    migration.create_checked(client, "pending", "来源配置ID", batch)
    assert client.calls == [batch, [batch[1]]]


def test_plan_preserves_numeric_options_and_inline_rear_option(monkeypatch, tmp_path):
    import hashlib
    import json

    import autohome_catalog.boolean_layout as layout

    def save(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False))

    root = tmp_path / "run"
    source = tmp_path / "source"
    seats = {"group": "参数信息", "section": "车身", "item_id": 1, "name": "座位数(个)"}
    bags = {"group": "安全配置", "section": "被动安全", "item_id": 2, "name": "前/后排侧气囊"}
    fog = {"group": "灯光", "section": "车外灯光", "item_id": 3, "name": "车前雾灯"}
    fields = [
        {
            "name": f["name"],
            "type": "number" if f is seats else "text",
            "description": f"汽车之家{f['group']}/{f['section']}；原始参数ID={f['item_id']}；原名={f['name']}。",
        }
        for f in [seats, bags, fog]
    ]
    manifest = {}
    for role in ["trims", "equipment"]:
        records = [
            {"record_id": f"{role}-{sid}", "fields": {"来源配置ID": sid}} for sid in ["1", "2"]
        ]
        path = root / "backup" / role / "records.json"
        save(path, records)
        manifest[role] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        save(root / "backup" / role / "schema.json", fields if role == "trims" else [])
        save(root / "backup" / role / "views.json", [{"visible": [f["name"] for f in fields]}])
    save(root / "backup-verification.json", manifest)
    save(source / "full-field-catalog.json", [seats, bags, fog])

    def key(f):
        return tuple(f[k] for k in ["group", "section", "item_id", "name"])

    cells = {
        "1": {
            key(seats): {
                "sublist": [
                    {"name": "6", "value": "●"},
                    {"name": "7", "value": "○", "priceinfo": "600元"},
                ]
            },
            key(bags): {"itemname": "前●/后○", "priceinfo": "4600元"},
            key(fog): {"itemname": "", "sublist": [{"name": "", "value": "●"}]},
        },
        "2": {
            key(seats): {"itemname": "-"},
            key(bags): {"itemname": "●"},
            key(fog): {"sublist": [{"name": "LED", "value": "●"}]},
        },
    }
    monkeypatch.setattr(layout, "load_cells", lambda *args: cells)
    plan = layout.build(root, source)
    assert plan["issues"] == []
    assert {(x["选装项目"], x["选装价格"]) for x in plan["options"]} == {
        ("7", "600元"),
        ("后", "4600元"),
    }
    assert all(x["是否标配"] is False for x in plan["options"])
    assert all(f["source_key"][-1] != "座位数(个)" for f in plan["features"])
    fog_status = next(f for f in plan["features"] if f["source_key"][-1] == "车前雾灯")
    assert fog_status["name"] == "车前雾灯标配"
    assert fog_status["preserve_source"] is True
    assert fog_status["values"] == {"1": True, "2": True}
    rear = next(f for f in plan["features"] if f["label"] == "后")
    assert rear["values"] == {"1": False, "2": None}


def test_detail_partition_preserves_every_row_and_keeps_each_model_together():
    packet = {
        "schema": [],
        "data": {
            f"{model}-{i}": {"model_id": model, "来源配置ID": str(i)}
            for model in ["A", "B", "C"]
            for i in range(3)
        },
    }
    chunks = partition_details(packet, limit=6)
    assert [len(p["data"]) for p in chunks] == [6, 3]
    assert set().union(*(p["data"].keys() for p in chunks)) == packet["data"].keys()
    owners = {}
    for index, chunk in enumerate(chunks):
        for f in chunk["data"].values():
            assert owners.setdefault(f["model_id"], index) == index


def test_hybrid_compilation_keeps_spec_and_boolean_adjacent_without_new_table(tmp_path):
    import json

    from autohome_catalog.boolean_layout import compile_tables

    identity = ["配置名称", "model_id", "来源配置ID", "车系名称", "品牌"]
    for role in ("trims", "equipment"):
        folder = tmp_path / "backup" / role
        folder.mkdir(parents=True)
        schema = [{"name": n, "type": "text"} for n in identity]
        if role == "equipment":
            schema.append({"name": "车前雾灯", "type": "text"})
        rows = [
            {"record_id": role + sid, "fields": dict.fromkeys(identity, sid) | {"车前雾灯": value}}
            for sid, value in [("1", "LED"), ("2", "●"), ("3", None)]
        ]
        for name, value in [
            ("schema", schema),
            ("records", rows),
            ("views", [{"visible": [f["name"] for f in schema]}]),
        ]:
            (folder / f"{name}.json").write_text(json.dumps(value))
    plan = {
        "issues": [],
        "options": [],
        "features": [
            {
                "name": "车前雾灯标配",
                "preserve_source": True,
                "source_role": "equipment",
                "source_field": "车前雾灯",
                "values": {"1": True, "2": True, "3": None},
            }
        ],
        "updates": {"trims": {}, "equipment": {}},
    }
    result = compile_tables(tmp_path, plan)
    assert len(result["packets"]) == 1
    mapping = next(m for m in result["mapping"] if m["source_field"] == "车前雾灯")
    assert mapping["fields"] == ["车前雾灯标配", "车前雾灯"]
    packet = result["packets"][0]
    assert packet["data"]["1"]["车前雾灯"] == "LED"
    assert packet["data"]["2"]["车前雾灯"] == ""
    assert packet["data"]["2"]["车前雾灯标配"] is True
    assert packet["data"]["3"]["车前雾灯标配"] is None


def test_image_url_readback_markdown_keeps_addresses_and_rejects_changes():
    urls = ["https://img.example/1.jpg", "https://img.example/2.jpg"]
    verify_fields(
        {"图片URL清单": "\n".join(urls)}, {"图片URL清单": "\n".join(f"[{u}]({u})" for u in urls)}
    )
    with pytest.raises(ValueError):
        verify_fields({"图片URL清单": urls[0]}, {"图片URL清单": urls[1]})
