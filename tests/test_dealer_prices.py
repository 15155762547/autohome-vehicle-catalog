from types import SimpleNamespace

import pytest

from autohome_catalog.dealer_prices import COLUMNS, guard, parse_quotes, records


def payload(prices):
    return {
        "returncode": 0,
        "result": {
            "list": [{"specid": sid, "askprice": {"newsprice": price}} for sid, price in prices]
        },
    }


def test_real_quote_and_explicit_no_quote():
    result = parse_quotes(payload([(78342, 145800), (1, 0), (2, 200000)]), [78342, 1, 2])
    assert result["78342"]["display"] == "14.58万起"
    assert result["1"]["display"] == "暂无报价"
    assert result["2"]["display"] == "20万起"


@pytest.mark.parametrize(
    "data,wanted",
    [
        (payload([(1, 100)]), [1, 2]),
        (payload([(1, 100), (1, 100)]), [1]),
        (payload([(2, 100)]), [1]),
        ({"returncode": 1}, [1]),
        (payload([(1, -1)]), [1]),
        (payload([(1, None)]), [1]),
        (payload([(1, float("nan"))]), [1]),
    ],
)
def test_incomplete_or_invalid_prices_are_not_no_quote(data, wanted):
    with pytest.raises((ValueError, KeyError)):
        parse_quotes(data, wanted)


def test_dealer_guard_only_updates_existing_trim_table():
    config = {"tables": {"trims": "trims"}}
    guard(config, "+record-batch-update", ["--table-id", "trims"])
    for command, args in [
        ("+record-batch-update", ["--table-id", "models"]),
        ("+record-batch-create", ["--table-id", "trims"]),
        ("+field-update", ["--table-id", "trims"]),
    ]:
        with pytest.raises(ValueError):
            guard(config, command, args)


def test_json_record_pagination_uses_supported_limit_and_stable_keys():
    def call(command, args):
        assert args[args.index("--limit") + 1] == "200"
        offset = int(args[args.index("--offset") + 1])
        return {
            "rev": 1,
            "fields": COLUMNS,
            "record_id_list": [f"rec{offset}"],
            "data": [[offset + 1, "配置", "AUT-1-BEV", "暂无报价"]],
            "has_more": offset == 0,
        }

    result = records(SimpleNamespace(config={"tables": {"trims": "trims"}}, call=call))
    assert set(result) == {"1", "2"}
    assert result["2"]["record_id"] == "rec1"
