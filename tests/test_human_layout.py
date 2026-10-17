import hashlib
import json

import pytest

from autohome_catalog.human_layout import (
    assert_migration_operation,
    build_layout,
    detail_key,
    read_records,
)


def test_layout_preserves_conditional_values_and_option_identity(tmp_path):
    source, target = tmp_path / "source", tmp_path / "migration"
    source.mkdir()
    target.mkdir()
    fields = [
        {
            "group": "参数信息",
            "section": "车身",
            "item_id": 1,
            "name": "长度(mm)",
            "column": "长度(mm)",
            "kind": "number",
            "table": "trims",
        },
        {
            "group": "配置",
            "section": "座椅配置",
            "item_id": 2,
            "name": "座椅加热",
            "column": "座椅加热",
            "kind": "text",
            "table": "equipment",
        },
        {
            "group": "配置",
            "section": "选装包",
            "item_id": 3,
            "name": "舒适包",
            "column": "舒适包",
            "kind": "text",
            "table": "equipment",
        },
    ]
    (source / "full-field-catalog.json").write_text(json.dumps(fields))
    for batch in ("main", "extension2", "extension3", "extension4", "extension5"):
        directory = source / "feishu-batches" / batch
        directory.mkdir(parents=True)
        (directory / "fields.json").write_text(
            json.dumps({"fields": fields if batch == "main" else []})
        )
    rows = {
        "models": [
            {"record_id": "m1", "fields": {"来源车系ID": "1", "车系名称": "测试车", "品牌": "品牌"}}
        ],
        "trims": [
            {
                "record_id": "t1",
                "fields": {
                    "来源配置ID": "10",
                    "来源车系ID": "1",
                    "配置名称": "基础版",
                    "长度(mm)": None,
                    "原文｜长度(mm)": "200–300",
                },
            }
        ],
        "equipment": [
            {
                "record_id": "e1",
                "fields": {
                    "来源配置ID": "10",
                    "配置名称": "基础版",
                    "座椅加热": "标配（●）",
                    "舒适包": "-",
                },
            }
        ],
    }
    manifest = []
    for role, data in rows.items():
        directory = target / "backup" / role
        directory.mkdir(parents=True)
        path = directory / "records.json"
        path.write_text(json.dumps(data))
        manifest.append({"role": role, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    (target / "backup-verification.json").write_text(
        json.dumps({"tables": manifest, "revisions_unchanged": True})
    )
    result = build_layout(source, target)
    trim = result["rows"]["trims"][0]["fields"]
    assert trim["长度(mm)"] is None
    assert trim["原文｜长度(mm)"] == "200–300"
    assert json.loads(trim["参数原文存档"])["长度(mm)"] == "200–300"
    assert result["rows"]["equipment"][0]["fields"]["座椅加热"] == "标配（●）"
    assert result["rows"]["special"][0]["fields"]["内容"] == "-"
    assert "舒适包" not in {f["name"] for s in result["schemas"].values() for f in s}
    assert detail_key("10", fields[2]) != detail_key("10", {**fields[2], "item_id": 4})
    (target / "backup/trims/records.json").write_text("[]")
    with pytest.raises(ValueError, match="Backup hash changed"):
        build_layout(source, target)


def test_migration_cannot_write_old_tables_or_arbitrary_targets():
    config = {
        "tables": {"models": "models"},
        "human_layout_migration": {
            "new_tables": {"trims": "new"},
            "archive_names": {"old": "旧表备份"},
        },
    }
    assert_migration_operation(config, "+record-batch-create", ["--table-id", "new"])
    for table in ("old", "models", "production"):
        with pytest.raises(ValueError, match="outside"):
            assert_migration_operation(config, "+record-batch-create", ["--table-id", table])
    with pytest.raises(ValueError, match="outside"):
        assert_migration_operation(
            config, "+base-block-rename", ["--block-id", "old", "--name", "wrong"]
        )


def test_paginated_read_restarts_when_async_writes_change_revision(monkeypatch):
    def page(rev, spec, more):
        return {
            "rev": rev,
            "fields": ["来源配置ID"],
            "record_id_list": ["r" + spec],
            "data": [[spec]],
            "has_more": more,
        }

    pages = iter([page(1, "1", True), page(2, "2", False), page(2, "1", True), page(2, "2", False)])

    class Client:
        def call(self, *args):
            return next(pages)

    monkeypatch.setattr("autohome_catalog.human_layout.time.sleep", lambda _: None)
    rows = read_records(Client(), "table", "来源配置ID")
    assert [r["fields"]["来源配置ID"] for r in rows] == ["1", "2"]
