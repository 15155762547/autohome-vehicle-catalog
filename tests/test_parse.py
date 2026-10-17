import copy

import pytest
from pydantic import ValidationError

from autohome_catalog.models import ParameterResponse
from autohome_catalog.parse import choose_series, numeric_cell, parse_parameters, render_cell


def payload():
    return {
        "bread": {"seriesid": 123, "seriesname": "测试车系", "brandname": "品牌"},
        "titlelist": [
            {
                "groupname": "参数信息",
                "itemtype": "车身",
                "items": [
                    {"itemid": 14, "titleid": 7, "itemname": "长度(mm)"},
                    {"itemid": 28, "titleid": 9, "itemname": "座位数(个)"},
                ],
            }
        ],
        "datalist": [
            {
                "specid": 81,
                "specname": "A",
                "specstatus": 20,
                "paramconflist": [
                    {"titleid": 7, "itemname": "4800"},
                    {"titleid": 9, "itemname": "", "sublist": [{"name": "5", "value": "●"}]},
                ],
            },
            {
                "specid": 82,
                "specname": "B",
                "specstatus": 20,
                "paramconflist": [{"titleid": 7, "itemname": "4900"}],
            },
        ],
    }


def test_maps_each_response_title_id_and_keeps_spec_values_separate():
    first = parse_parameters(payload(), 123, {81, 82})
    key = ("参数信息", "车身", 14, "长度(mm)")
    assert first[81][key].itemname == "4800"
    assert first[82][key].itemname == "4900"
    changed = payload()
    changed["titlelist"][0]["items"][0]["titleid"] = 90
    for spec in changed["datalist"]:
        spec["paramconflist"][0]["titleid"] = 90
    second = parse_parameters(changed, 123, {81, 82})
    assert [second[i][key].itemname for i in [81, 82]] == ["4800", "4900"]
    assert second[81][key].titleid == 90


def test_wrong_series_missing_spec_and_duplicate_spec_are_rejected():
    with pytest.raises(ValueError, match="series"):
        parse_parameters(payload(), 124, {81})
    with pytest.raises(ValueError, match="Missing"):
        parse_parameters(payload(), 123, {99})
    p = payload()
    p["datalist"].append(copy.deepcopy(p["datalist"][0]))
    with pytest.raises(ValueError, match="Duplicate"):
        parse_parameters(p, 123, {81})


def test_bad_source_shape_is_pydantic_validated():
    p = payload()
    p["datalist"][0]["specid"] = "not-an-id"
    with pytest.raises(ValidationError):
        ParameterResponse.model_validate(p)


def test_options_positions_and_unknown_dash_are_not_booleans():
    assert render_cell({"itemname": "前●/后-"}) == "前标配（●）/后-"
    assert render_cell({"itemname": "○", "priceinfo": "2000元"}) == "选配（○）；价格条件：2000元"
    assert (
        render_cell({"itemname": "", "sublist": [{"name": "加热", "value": "○"}]})
        == "加热选配（○）"
    )
    assert render_cell({"itemname": "-"}) == "-"
    assert numeric_cell({"itemname": "-"}) is None
    assert numeric_cell({"itemname": "", "sublist": [{"name": "5", "value": "●"}]}) == 5
    assert numeric_cell({"itemname": "30-80"}) is None
    assert numeric_cell({"itemname": "", "sublist": [{"name": "5", "value": "○"}]}) is None


def test_in_sale_plus_explicit_presale_does_not_include_all_state_10():
    rows = [
        {"seriesid": i, "seriesname": str(i), "seriesstate": state, "newenergy": 1}
        for i, state in [(1, 20), (2, 40), (3, 0), (4, 10), (5, 10)]
    ]
    chosen, review = choose_series(rows, {4})
    assert {x["seriesid"] for x in chosen} == {1, 4}
    assert {x["seriesid"] for x in review} == {3, 5}


def test_in_sale_variants_include_state_30_and_presale_override_cannot_revive_discontinued():
    rows = [
        {"seriesid": 10, "seriesname": "在售", "seriesstate": 30, "newenergy": 1},
        {"seriesid": 11, "seriesname": "停售", "seriesstate": 40, "newenergy": 1},
    ]
    chosen, _ = choose_series(rows, {11})
    assert [r["seriesid"] for r in chosen] == [10]
