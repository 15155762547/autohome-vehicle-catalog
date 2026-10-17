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
    assert any(f["name"] == "长度(mm)" and f["type"] == "number" for f in schemas["trims"])


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


def test_schema_extension_adds_only_missing_fields_and_refuses_type_or_link_changes():
    from autohome_catalog.feishu import schema_delta

    expected = [{"name": "数值", "type": "number"}, {"name": "原文", "type": "text"}]
    assert schema_delta(expected, [{"name": "数值", "type": "number"}]) == [expected[1]]
    with pytest.raises(ValueError, match="Schema"):
        schema_delta(expected, [{"name": "数值", "type": "text"}])
    with pytest.raises(ValueError, match="Schema"):
        schema_delta(
            [{"name": "关联", "type": "link", "link_table": "a"}],
            [{"name": "关联", "type": "link", "link_table": "b"}],
        )


def test_verification_checks_both_directions_of_model_trim_and_extension_links():
    from autohome_catalog.feishu import verify_relations

    desired = {
        "trims": [{"key": "2", "model_key": "1"}],
        "equipment": [{"key": "2", "trim_key": "2"}],
    }
    snapshots = {
        "models": [{"record_id": "m", "fields": {"来源车系ID": "1", "配置列表": [{"id": "t"}]}}],
        "trims": [{"record_id": "t", "fields": {"来源配置ID": "2", "参数扩展": [{"id": "e"}]}}],
        "equipment": [{"record_id": "e", "fields": {"来源配置ID": "2"}}],
    }
    assert verify_relations(desired, snapshots) == 2
    snapshots["models"][0]["fields"]["配置列表"] = []
    with pytest.raises(ValueError, match="Reverse"):
        verify_relations(desired, snapshots)


def test_schema_readback_waits_for_new_columns_without_recreating_them(monkeypatch):
    import autohome_catalog.feishu as module
    from autohome_catalog.feishu import Client

    c = config()
    definitions = table_schemas(load_dictionary(), c["tables"])
    calls = {r: 0 for r in definitions}

    class ReadOnlyClient:
        config = c

        def call(self, command, args):
            assert command == "+field-list"
            role = next(r for r, i in c["tables"].items() if i == args[1])
            calls[role] += 1
            actual = definitions[role]
            if role == "trims" and calls[role] == 1:
                actual = actual[:-1]
            return {"fields": actual}

    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    Client.validate_schemas(ReadOnlyClient(), load_dictionary())
    assert calls["trims"] == 2


def test_record_visibility_delay_does_not_cause_duplicate_writes(tmp_path, monkeypatch):
    import json

    import autohome_catalog.feishu as module

    desired = {
        "models": [{"key": "1", "fields": {"来源车系ID": "1", "车系名称": "样例"}}],
        "trims": [],
        "equipment": [],
    }
    initial = {"models": [], "trims": [], "equipment": []}
    c = config()
    (tmp_path / "desired.json").write_text(json.dumps(desired))
    (tmp_path / "feishu-plan.json").write_text(
        json.dumps(
            {
                "desired_sha256": module.digest(desired),
                "config_sha256": module.digest(c),
                "dictionary_sha256": module.digest(load_dictionary()),
                "snapshot_sha256": module.digest(initial),
            }
        )
    )

    class EventuallyVisibleClient:
        config = c
        writes = 0
        reads = 0

        def validate_schemas(self, _):
            pass

        def records(self, role):
            if role != "models":
                return []
            self.reads += 1
            if self.reads <= 6:
                return []
            return [{"record_id": "m1", "fields": desired["models"][0]["fields"]}]

        def call(self, command, args, body):
            assert command == "+record-batch-create"
            self.writes += 1
            return {"record_id_list": ["m1"]}

    client = EventuallyVisibleClient()
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    module.apply(client, tmp_path)
    assert client.writes == 1
