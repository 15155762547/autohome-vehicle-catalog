import pytest

from autohome_catalog.feishu import reject_dimension_restore
from autohome_catalog.multi_select import guard, tokens


def test_standard_features_become_individual_labels_without_splitting_slashes():
    assert tokens("运动 标配\n标准/舒适 标配\n雪地 标配") == ["运动", "标准/舒适", "雪地"]
    assert tokens("USB 标配\nType-C 标配") == ["USB", "Type-C"]


def test_optional_prices_and_whole_field_conditions_are_preserved():
    assert tokens("加热 标配\n按摩 选配（3000元）\n价格条件：组合5000元") == [
        "加热",
        "按摩（选配，3000元）",
        "价格条件：组合5000元",
    ]
    assert tokens("选配（1500元）") == ["支持（选配，未列明功能，1500元）"]


def test_position_pairs_negative_and_unknown_are_distinct():
    assert tokens("前 标配/后 选配") == ["前", "后（选配）"]
    assert tokens("主 标配/副-") == ["主", "副（无）"]
    assert tokens("-") == ["无"]
    assert tokens(None) == []
    assert tokens("运动 标配\n运动 标配") == ["运动"]
    with pytest.raises(ValueError):
        tokens("无法识别的自由文本")


def test_source_standard_prices_are_not_discarded():
    assert tokens("通风 标配（价格条件详询）") == ["通风（价格条件详询）"]


def test_guard_keeps_existing_status_fields_outside_conversion():
    config = {"_multi_select": {"table": ["compound"]}}
    guard(config, "+field-update", ["--table-id", "table", "--field-id", "compound"])
    with pytest.raises(ValueError):
        guard(config, "+field-update", ["--table-id", "table", "--field-id", "status"])


@pytest.mark.parametrize(
    "command,body",
    [
        ("+field-create", {"name": "长度(mm)", "type": "number"}),
        ("+field-create", [{"name": "宽度(mm)", "type": "number"}]),
        ("+table-create", {"fields": [{"name": "高度(mm)", "type": "number"}]}),
        ("+field-update", {"name": "长度(mm)", "type": "number"}),
    ],
)
def test_user_deleted_dimension_columns_cannot_be_restored(command, body):
    with pytest.raises(ValueError, match="User removed"):
        reject_dimension_restore(command, body)
    reject_dimension_restore("+field-create", {"name": "长*宽*高(mm)", "type": "text"})
