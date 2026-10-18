from types import SimpleNamespace

import pytest

from autohome_catalog.number_format import guard, precision, read_numbers


def test_minimum_precision_retains_real_fractions():
    assert precision([5, 5.0, 0, None]) == 0
    assert precision([5, 2.5, 0.01, 8.1200]) == 2
    assert precision([0.0001, 1000]) == 4
    assert precision([None, ""]) is None
    for value in [True, "5", float("nan")]:
        with pytest.raises(ValueError):
            precision([value])


def test_guard_only_allows_whitelisted_field_updates():
    config = {"_number_format": [["table", "field"]]}
    guard(config, "+field-update", ["--table-id", "table", "--field-id", "field"])
    with pytest.raises(ValueError):
        guard(config, "+field-update", ["--table-id", "other", "--field-id", "field"])
    with pytest.raises(ValueError):
        guard(config, "+record-batch-update", ["--table-id", "table"])


def test_numeric_projection_batches_at_most_50_columns_and_merges_by_key():
    calls = []

    def call(command, args):
        columns = [args[i + 1] for i, arg in enumerate(args) if arg == "--field-id"]
        assert len(columns) <= 50
        calls.append(columns)
        return {
            "rev": 1,
            "record_id_list": ["rec1"],
            "fields": columns,
            "data": [["key" if name == "来源配置ID" else 5 for name in columns]],
            "has_more": False,
        }

    client = SimpleNamespace(config={"tables": {"trims": "table"}}, call=call)
    result = read_numbers(client, "trims", [{"id": f"f{i}"} for i in range(81)])
    assert len(calls) == 2
    assert len(result["key"]["fields"]) == 82
    assert result["key"]["record_id"] == "rec1"
