import pytest

from autohome_catalog.single_select import choice, specification, state, words


@pytest.mark.parametrize(
    "cell,expected",
    [
        ({"itemname": "●"}, "标配"),
        ({"itemname": "○"}, "选配"),
        ({"itemname": "-"}, "无"),
        ({"itemname": ""}, None),
        (None, None),
        ({"sublist": [{"name": "无", "value": "●"}]}, "无"),
    ],
)
def test_three_source_states_and_unknown(cell, expected):
    assert state(cell) == expected


def test_led_standard_and_optional_laser_are_not_conflated():
    cell = {
        "sublist": [
            {"name": "LED", "value": "●"},
            {"name": "激光", "value": "○", "priceinfo": "10000元"},
        ]
    }
    assert specification(cell) == ("LED", ["标配"], "激光（10000元）")
    assert specification({"sublist": [{"name": "LED", "value": "○"}]}) == ("LED", ["选配"], None)
    assert specification({"itemname": "●"}) == (None, ["标配"], None)
    assert specification({"itemname": "-"}) == (None, ["无"], None)
    assert specification(None) == (None, None, None)


def test_compound_text_keeps_positions_states_and_prices():
    text = words(
        "主座椅调节方式",
        {
            "sublist": [
                {"name": "前后调节", "value": "●"},
                {"name": "腰部支撑(4向)", "value": "○", "priceinfo": "1000元"},
            ]
        },
    )
    assert text == "前后 标配\n腰撑(4向) 选配（1000元）"
    assert not any(flag in text for flag in "●○")
    f = choice("座椅")
    assert f["type"] == "select" and f["multiple"] is False
    assert [x["name"] for x in f["options"]] == ["标配", "选配", "无"]
