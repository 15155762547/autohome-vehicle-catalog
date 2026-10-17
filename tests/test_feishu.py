import pytest

from autohome_catalog.export import load_dictionary
from autohome_catalog.feishu import assert_target, diff_records, same_value, table_schemas


def config():
    return {
        "purpose": "experiment",
        "profile": "shengxu",
        "base_token": "lab",
        "allowed_base_token": "lab",
        "production_base_tokens": ["production"],
        "tables": {"models": "m", "trims": "t", "equipment": "e"},
        "legacy_tables": {"models": "old-m", "trims": "old-t"},
    }


def test_production_and_legacy_targets_are_rejected():
    c = config()
    assert_target(c)
    c["base_token"] = "production"
    with pytest.raises(ValueError, match="target"):
        assert_target(c)
    c = config()
    c["tables"]["models"] = "old-m"
    with pytest.raises(ValueError, match="legacy"):
        assert_target(c)


def test_stable_keys_prevent_duplicate_creates_and_only_changed_cells_are_updated():
    desired = [{"key": "81", "fields": {"来源配置ID": "81", "功率": 50, "选装": "选配（○）"}}]
    current = [{"record_id": "r1", "fields": {"来源配置ID": "81", "功率": 50, "选装": "标配（●）"}}]
    changes = diff_records(desired, current, "来源配置ID")
    assert changes == {"create": [], "update": {"r1": {"选装": "选配（○）"}}}
    current[0]["fields"]["选装"] = "选配（○）"
    assert diff_records(desired, current, "来源配置ID") == {"create": [], "update": {}}
    with pytest.raises(ValueError, match="Duplicate"):
        diff_records(desired, current + current, "来源配置ID")


def test_datetime_timezone_and_unordered_links_are_equal():
    assert same_value("2026-10-17T00:00:00Z", "2026-10-17T08:00:00+08:00")
    assert same_value([{"id": "a"}, {"id": "b"}], [{"id": "b"}, {"id": "a"}])
    assert not same_value(None, False)


def test_schemas_have_chinese_independent_fields_and_no_attachment_or_airbag_total():
    schemas = table_schemas(load_dictionary(), config()["tables"])
    assert all(len(s) < 300 for s in schemas.values())
    assert all(f["type"] != "attachment" for s in schemas.values() for f in s)
    assert all(f["name"] != "气囊总数" for s in schemas.values() for f in s)
    assert not any("长度" in f["name"] for f in schemas["models"])
    assert any(f["name"] == "车身｜长度(mm)" and f["type"] == "number" for f in schemas["trims"])


def test_each_write_command_checks_actual_table_id_and_disallows_deletion():
    from autohome_catalog.feishu import assert_operation

    assert_operation(config(), "+record-batch-create", ["--table-id", "t"])
    with pytest.raises(ValueError, match="table"):
        assert_operation(config(), "+record-batch-create", ["--table-id", "old-t"])
    with pytest.raises(ValueError, match="command"):
        assert_operation(config(), "+record-delete", ["--table-id", "t"])


def test_url_markdown_readback_matches_original_bare_url():
    url = "https://www.autohome.com.cn/5346/"
    assert same_value(f"[{url}]({url})", url)
    assert not same_value("[source](https://example.test/other)", url)
