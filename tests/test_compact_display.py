from autohome_catalog.compact_display import compact


def test_seat_conditions_prices_positions_and_statuses_survive():
    text = "高低调节(4向)标配（●）；腰部支撑(4向)选配（○）（4500元）；按摩(仅副驾驶位)标配（●）"
    expected = "高低(4向) ●\n腰撑(4向) ○（4500元）\n按摩(仅副驾) ●"
    assert compact("主座椅调节方式", text) == expected
    assert compact("主座椅调节方式", expected) == expected


def test_unspecified_app_support_does_not_invent_features():
    assert compact("手机APP远程功能", "标配（●）") == "●"
    assert compact("手机APP远程功能", "-") == "-"
    assert compact("手机APP远程功能", None) is None


def test_source_evidence_and_numeric_values_stay_untouched():
    text = "功能标配（●）；条件原文"
    for name in ["来源参数页", "原文｜功能", "参数原文存档"]:
        assert compact(name, text) == text
    assert compact("功率", 123) == 123


def test_condition_punctuation_is_not_split():
    text = "车主服务（查找充电桩；停车场等）标配（●）；预约保养/维修选配（○）"
    assert compact("手机APP远程功能", text) == "车主服务（查找充电桩；停车场等） ●\n预约保养/维修 ○"
