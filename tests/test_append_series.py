import pytest

from autohome_catalog.append_series import guard, slot_values


def test_compound_equipment_preserves_options_and_prices():
    key = ("操控配置", "驾驶操控", 197, "驾驶模式切换")
    cell = {
        "itemname": "",
        "sublist": [
            {"name": "运动", "value": "●"},
            {"name": "雪地", "value": "○", "priceinfo": "500元"},
        ],
    }
    m = {"fields": ["驾驶模式"]}
    value = slot_values(
        cell, key, m, {"type": "text"}, {"驾驶模式": {"type": "select", "multiple": True}}
    )
    assert value == {"驾驶模式": ["运动", "雪地（选配，500元）"]}


def test_spec_and_state_are_written_to_separate_existing_columns():
    cell = {"itemname": "", "sublist": [{"name": "LED", "value": "●"}]}
    m = {"fields": ["近光灯光源", "近光灯状态", "近光灯光源选配规格"]}
    v = slot_values(cell, ("外部配置", "车外灯光", 174, "近光灯光源"), m, {"type": "text"}, {})
    assert v == {"近光灯光源": "LED", "近光灯状态": ["标配"], "近光灯光源选配规格": None}


def test_absent_state_is_distinct_from_explicit_absence():
    m = {"fields": ["自动头灯"]}
    key = ("外部配置", "车外灯光", 78, "自动头灯")
    assert slot_values(None, key, m, {"type": "select"}, {})["自动头灯"] is None
    assert slot_values({"itemname": "-"}, key, m, {"type": "select"}, {})["自动头灯"] == ["无"]


def test_append_guard_never_allows_row_updates_or_schema_deletions():
    c = {"_append_series": {"tables": ["new"]}}
    guard(c, "+record-batch-create", ["--table-id", "new"])
    for command, tid in [
        ("+record-batch-update", "new"),
        ("+record-delete", "new"),
        ("+field-delete", "new"),
        ("+record-batch-create", "other"),
    ]:
        with pytest.raises(ValueError):
            guard(c, command, ["--table-id", tid])
