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


@pytest.mark.parametrize("role,key", [("车身", "来源配置ID"), ("选装包", "明细ID")])
def test_category_number_reads_use_configuration_or_detail_key(role, key):
    def call(command, args):
        columns = [args[i + 1] for i, arg in enumerate(args) if arg == "--field-id"]
        assert columns[0] == key
        return {
            "rev": 1,
            "record_id_list": ["rec1"],
            "fields": columns,
            "data": [["key", 5]],
            "has_more": False,
        }

    client = SimpleNamespace(config={"tables": {role: "category"}}, call=call)
    assert read_numbers(client, role, [{"id": "number"}])["key"]["fields"]["number"] == 5
