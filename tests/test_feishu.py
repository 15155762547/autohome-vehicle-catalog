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
    assert not any(f["name"] in {"长度(mm)", "宽度(mm)", "高度(mm)"} for f in schemas["trims"])
    assert any(f["name"] == "长*宽*高(mm)" for f in schemas["trims"])


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


def test_extra_tables_and_view_writes_stay_inside_explicit_experiment_scope():
    from autohome_catalog.feishu import assert_operation

    c = config()
    with pytest.raises(ValueError, match="name"):
        assert_operation(c, "+table-create", ["--name", "配置参数扩展2"])
    c["extension_table_names"] = ["配置参数扩展2", "unrelated"]
    assert_operation(c, "+table-create", ["--name", "配置参数扩展2"])
    with pytest.raises(ValueError, match="name"):
        assert_operation(c, "+table-create", ["--name", "unrelated"])
    with pytest.raises(ValueError, match="table"):
        assert_operation(c, "+view-set-visible-fields", ["--table-id", "old-t"])


def test_extension_verification_checks_its_own_reverse_link():
    from autohome_catalog.feishu import verify_relations

    desired = {"trims": [], "equipment": [{"key": "2", "trim_key": "2"}]}
    snapshots = {
        "models": [],
        "trims": [
            {
                "record_id": "t",
                "fields": {
                    "来源配置ID": "2",
                    "参数扩展": [{"id": "original"}],
                    "参数扩展2": [{"id": "extra"}],
                },
            }
        ],
        "equipment": [{"record_id": "extra", "fields": {"来源配置ID": "2"}}],
    }
    assert verify_relations(desired, snapshots, "参数扩展2") == 1
    with pytest.raises(ValueError, match="Reverse"):
        verify_relations(desired, snapshots)


def test_pagination_restarts_when_async_commits_change_revision(monkeypatch):
    import autohome_catalog.feishu as module

    class ReadChangingTable(module.Client):
        def __init__(self):
            self.config = config()
            self.reads = 0

        def call(self, command, args):
            assert command == "+record-list"
            self.reads += 1
            if self.reads < 3:
                return {
                    "rev": self.reads,
                    "record_id_list": ["r1"],
                    "data": [["1"]],
                    "fields": ["来源车系ID"],
                    "has_more": True,
                }
            return {
                "rev": 3,
                "record_id_list": ["r1", "r2"],
                "data": [["1"], ["2"]],
                "fields": ["来源车系ID"],
                "has_more": False,
            }

    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    client = ReadChangingTable()
    assert len(client.records("models")) == 2
    assert client.reads == 3


def test_extension_snapshot_reads_parent_ids_and_reverse_link_without_unrelated_parameters():
    from autohome_catalog.feishu import snapshots_for

    class ProjectionClient:
        config = {"equipment_reverse_field": "参数扩展2"}
        calls = []

        def records(self, role, field_names=None):
            self.calls.append((role, field_names))
            return []

    client = ProjectionClient()
    snapshots_for(client, {"models": [], "trims": [], "equipment": [{"key": "2"}]})
    assert client.calls == [("trims", ["来源配置ID", "参数扩展2"]), ("equipment", None)]


@pytest.mark.parametrize("error", [{"code": 800100001}, {"code": 2200, "retryable": True}])
def test_transient_read_retries_but_uncertain_record_write_is_never_blindly_replayed(
    tmp_path, monkeypatch, error
):
    import json
    import subprocess

    import autohome_catalog.feishu as module

    path = tmp_path / "config.json"
    path.write_text(json.dumps(config()))
    attempts = {"+field-list": 0, "+record-batch-create": 0}

    def execute(argv, **kwargs):
        command = argv[2]
        attempts[command] += 1
        if command == "+field-list" and attempts[command] == 2:
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"ok": True, "identity": "user", "data": {"fields": []}}), ""
            )
        return subprocess.CompletedProcess(
            argv, 1, "", json.dumps({"ok": False, "identity": "user", "error": error})
        )

    monkeypatch.setattr(module.subprocess, "run", execute)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    client = module.Client(path)
    assert client.call("+field-list", ["--table-id", "t"]) == {"fields": []}
    with pytest.raises(RuntimeError):
        client.call(
            "+record-batch-create", ["--table-id", "t"], {"create_records": [{"来源配置ID": "2"}]}
        )
    assert attempts == {"+field-list": 2, "+record-batch-create": 1}


@pytest.mark.parametrize("command", ["+view-get-filter", "+record-batch-create"])
def test_transport_eof_only_retries_reads(tmp_path, monkeypatch, command):
    import json
    import subprocess

    import autohome_catalog.feishu as module

    settings = config()
    settings["human_layout_migration"] = {"new_tables": {"trims": "t"}, "archive_names": {}}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(settings))
    attempts = []

    def execute(argv, **kwargs):
        attempts.append(argv)
        if len(attempts) == 1:
            error = {"type": "network", "subtype": "transport", "message": "EOF"}
            return subprocess.CompletedProcess(argv, 1, "", json.dumps({"error": error}))
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"ok": True, "identity": "user", "data": {}}), ""
        )

    monkeypatch.setattr(module.subprocess, "run", execute)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    client = module.Client(path)
    if command == "+view-get-filter":
        assert client.call(command, ["--table-id", "t"]) == {}
        assert len(attempts) == 2
    else:
        with pytest.raises(RuntimeError):
            client.call(command, ["--table-id", "t"])
        assert len(attempts) == 1
