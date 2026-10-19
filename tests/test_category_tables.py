import json

import pytest

from autohome_catalog.category_tables import (
    definition,
    guard,
    link,
    mapping_name,
    validate_mapping,
    verify_category_links,
)


def test_copy_field_preserves_type_precision_and_option_colors():
    field = {
        "id": "old",
        "name": "模式",
        "type": "select",
        "multiple": True,
        "options": [{"name": "雪地", "hue": "Blue", "lightness": "Lighter"}],
        "truncated": False,
    }
    copied = definition(field)
    assert copied["options"] == field["options"] and copied["multiple"] is True
    assert "id" not in copied and "truncated" not in copied
    copied["options"][0]["name"] = "changed"
    assert field["options"][0]["name"] == "雪地"
    number = {"name": "功率", "type": "number", "style": {"type": "plain", "precision": 2}}
    assert definition(number) == number
    with pytest.raises(ValueError):
        definition({"name": "长度(mm)", "type": "number"})
    assert mapping_name("电池/充电") == "电池／充电"


def test_category_guard_preserves_original_records_and_unrelated_tables():
    c = {
        "tables": {"models": "models"},
        "_categories": {
            "tables": {"基本参数": "new"},
            "source_tables": {"models": "models", "trims": "old"},
            "pending_names": ["基本参数（分类整理中）"],
            "model_fields": ["newlink", "oldlink"],
            "model_views": ["default"],
        },
    }
    guard(c, "+record-batch-create", ["--table-id", "new"])
    guard(c, "+field-create", ["--table-id", "new"])
    guard(c, "+base-block-move", ["--block-id", "old", "--parent-id", "archive"])
    guard(c, "+field-update", ["--table-id", "models", "--field-id", "newlink"])
    guard(c, "+view-set-visible-fields", ["--table-id", "models", "--view-id", "default"])
    for command, args in [
        ("+record-delete", ["--table-id", "new"]),
        ("+record-batch-update", ["--table-id", "old"]),
        ("+field-create", ["--table-id", "old"]),
        ("+record-batch-update", ["--table-id", "models"]),
        ("+base-block-move", ["--block-id", "models"]),
        ("+field-update", ["--table-id", "models", "--field-id", "images"]),
        ("+view-set-visible-fields", ["--table-id", "models", "--view-id", "unknown"]),
    ]:
        with pytest.raises(ValueError):
            guard(c, command, args)
    assert link("对应配置", "index", "车身")["bidirectional_link_field_name"] == "车身"


@pytest.mark.parametrize("problem", [None, "forward", "backlink", "identity"])
def test_category_links_require_exact_reciprocal_configuration_identity(
    tmp_path, monkeypatch, problem
):
    root = tmp_path
    (root / "verified").mkdir()
    category = {
        "record_id": "category-row",
        "fields": {
            "来源配置ID": 123,
            "对应配置": [{"id": "wrong" if problem == "forward" else "index-row"}],
        },
    }
    if problem == "identity":
        category["fields"]["来源配置ID"] = 999
    (root / "verified" / "车身.json").write_text(json.dumps({"123": category}))
    index = [
        {
            "record_id": "index-row",
            "fields": {
                "来源配置ID": 123,
                "车身": [{"id": "wrong" if problem == "backlink" else "category-row"}],
            },
        }
    ]
    monkeypatch.setattr("autohome_catalog.category_tables.read_records", lambda *a: index)
    state = {"tables": {"配置索引": "index"}}
    if problem:
        with pytest.raises(ValueError):
            verify_category_links(None, root, state, {"sections": ["车身"]})
        assert "category_links_verified" not in state
    else:
        verify_category_links(None, root, state, {"sections": ["车身"]})
        assert state["category_links_verified"] is True


@pytest.mark.parametrize("problem", [None, "missing", "duplicate", "unknown_category"])
def test_mapping_cannot_lose_or_duplicate_source_columns(problem):
    meta = {
        "roles": {
            role: {"fields": [{"name": "model_id"}, {"name": "参数"}]}
            for role in ["trims", "equipment"]
        }
    }
    mapping = {
        "sections": ["车身"],
        "metadata_fields": ["model_id"],
        "fields": [
            {"role": role, "name": "参数", "section": "车身"} for role in ["trims", "equipment"]
        ],
    }
    if problem == "missing":
        mapping["fields"].pop()
    elif problem == "duplicate":
        mapping["fields"].append(dict(mapping["fields"][0]))
    elif problem == "unknown_category":
        mapping["fields"][0]["section"] = "未知"
    if problem:
        with pytest.raises(ValueError):
            validate_mapping(meta, mapping)
    else:
        validate_mapping(meta, mapping)
