import pytest
from test_parse import payload

from autohome_catalog.export import assert_dictionary, expand_packet


def packet():
    return {
        "series": {
            "seriesid": 123,
            "seriesname": "测试车系",
            "brand_id": 1,
            "scope_status": "在售",
            "scope_basis": "源站在售目录",
            "seriesimg": "",
        },
        "spec_list": [
            {"id": 81, "specname": "A", "specstate": 20},
            {"id": 82, "specname": "B", "specstate": 20},
        ],
        "parameter_source": {
            "url": "https://example.test/parameters",
            "fetched_at": "2026-10-17T00:00:00+00:00",
            "sha256": "a" * 64,
        },
        "payload": payload(),
    }


def fields():
    return [
        {
            "group": "参数信息",
            "section": "车身",
            "item_id": 14,
            "name": "长度(mm)",
            "column": "长度(mm)",
            "table": "trims",
            "kind": "number",
        },
        {
            "group": "参数信息",
            "section": "车身",
            "item_id": 28,
            "name": "座位数(个)",
            "column": "座位数(个)",
            "table": "trims",
            "kind": "number",
        },
    ]


def test_expansion_uses_each_spec_and_does_not_fill_from_representative():
    expanded, issues = expand_packet(packet(), fields())
    assert issues == []
    assert expanded["models"][0]["fields"]["来源车系ID"] == "123"
    assert "长度(mm)" not in expanded["models"][0]["fields"]
    assert [r["fields"]["长度(mm)"] for r in expanded["trims"]] == [4800, 4900]
    assert [r["fields"]["座位数(个)"] for r in expanded["trims"]] == [5, None]


def test_new_field_is_reported_without_silently_dropping_it():
    _, issues = expand_packet(packet(), fields()[:1])
    assert issues[0]["kind"] == "unknown_field"
    assert issues[0]["field"][3] == "座位数(个)"


def test_dictionary_rejects_duplicate_keys_and_column_overflow():
    with pytest.raises(ValueError, match="Duplicate"):
        assert_dictionary(fields() + fields())
    many = [{**fields()[0], "item_id": i, "column": str(i)} for i in range(310)]
    with pytest.raises(ValueError, match="300"):
        assert_dictionary(many)


def test_feishu_datetime_keeps_millisecond_precision_without_changing_raw_evidence():
    p = packet()
    p["parameter_source"]["fetched_at"] = "2026-10-17T00:00:00.123456+00:00"
    expanded, _ = expand_packet(p, fields())
    assert expanded["models"][0]["fields"]["来源采集时间"] == "2026-10-17T00:00:00.123+00:00"
    assert expanded["evidence"][0]["source"]["fetched_at"] == "2026-10-17T00:00:00.123456+00:00"


def test_export_rejects_missing_completed_series_artifacts(tmp_path):
    import json

    from autohome_catalog.export import export_run

    (tmp_path / "status.json").write_text(
        json.dumps({"phase": "complete", "completed_series": [123]})
    )
    (tmp_path / "series").mkdir()
    with pytest.raises(ValueError, match="scope"):
        export_run(tmp_path)


def test_numeric_dash_has_visible_raw_column_and_is_not_invented_as_zero():
    p = packet()
    p["payload"]["datalist"][0]["paramconflist"][0]["itemname"] = "-"
    expanded, issues = expand_packet(p, fields())
    assert expanded["trims"][0]["fields"]["长度(mm)"] is None
    assert expanded["trims"][0]["fields"]["原文｜长度(mm)"] == "-"
    assert issues == []


def test_numeric_value_with_options_is_audited_and_raw_conditions_remain_visible():
    p = packet()
    p["payload"]["datalist"][0]["paramconflist"][0]["sublist"] = [
        {"name": "4900", "value": "○", "priceinfo": "2000元"}
    ]
    expanded, issues = expand_packet(p, fields())
    assert expanded["trims"][0]["fields"]["长度(mm)"] is None
    assert "4900○（2000元）" in expanded["trims"][0]["fields"]["原文｜长度(mm)"]
    assert issues[0]["kind"] == "numeric_condition"


def test_export_rejects_payload_changed_after_raw_snapshot_capture(tmp_path):
    import hashlib
    import json

    from autohome_catalog.export import export_run

    p = packet()
    body = json.dumps({"returncode": 0, "result": p["payload"]}).encode()
    source = {
        **p["parameter_source"],
        "file": "response.raw",
        "sha256": hashlib.sha256(body).hexdigest(),
    }
    p["parameter_source"] = source
    p["spec_list_source"] = source
    p["series"]["catalog_source"] = source
    p["payload"]["datalist"][0]["paramconflist"][0]["itemname"] = "9999"
    (tmp_path / "response.raw").write_bytes(body)
    (tmp_path / "series").mkdir()
    (tmp_path / "series/123.json").write_text(json.dumps(p))
    (tmp_path / "status.json").write_text(
        json.dumps({"phase": "complete", "completed_series": [123]})
    )
    dictionary = tmp_path / "dictionary.json"
    dictionary.write_text(json.dumps({"fields": fields()}))
    with pytest.raises(ValueError, match="payload"):
        export_run(tmp_path, dictionary)
