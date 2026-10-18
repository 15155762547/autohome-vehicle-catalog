from types import SimpleNamespace

import pytest

from autohome_catalog.primary_titles import full_title, guard, settled_fields


@pytest.mark.parametrize(
    "brand,series,expected",
    [
        ("问界", "问界M9", "问界M9 2026款 示例版"),
        ("宝马", "宝马i4", "宝马i4 2026款 示例版"),
        ("特斯拉", "Model 3", "特斯拉 Model 3 2026款 示例版"),
        ("smart", "SMART 精灵#1", "SMART 精灵#1 2026款 示例版"),
        ("AITO 问界", "问界M9", "AITO 问界M9 2026款 示例版"),
        ("FREELANDER 神行者", "神行者8", "FREELANDER 神行者8 2026款 示例版"),
        ("品牌", "车系品牌名", "品牌 车系品牌名 2026款 示例版"),
    ],
)
def test_title_only_omits_explicit_leading_brand(brand, series, expected):
    fields = {"品牌": brand, "车系名称": series, "配置名称": "2026款 示例版"}
    assert full_title(fields) == expected
    assert fields["品牌"] == brand


def test_missing_components_are_not_silently_dropped():
    with pytest.raises(ValueError):
        full_title({"品牌": "品牌", "车系名称": "车系"})


def test_guard_cannot_rename_unrelated_fields_or_delete_records():
    config = {"_primary_titles": {"table": "primary"}}
    guard(config, "+field-update", ["--table-id", "table", "--field-id", "primary"])
    for command, args in [
        ("+field-update", ["--table-id", "table", "--field-id", "other"]),
        ("+record-delete", ["--table-id", "table"]),
        ("+record-batch-update", ["--table-id", "other"]),
    ]:
        with pytest.raises(ValueError):
            guard(config, command, args)


def test_waits_for_field_rename_to_propagate(monkeypatch):
    monkeypatch.setattr("autohome_catalog.primary_titles.time.sleep", lambda _: None)
    responses = iter([[{"id": "id", "name": "配置名称"}], [{"id": "id", "name": "车型全称"}]])
    client = SimpleNamespace(call=lambda *args: {"fields": next(responses)})
    fields = settled_fields(client, "table", lambda fs: fs[0]["name"] == "车型全称")
    assert fields[0]["id"] == "id"
