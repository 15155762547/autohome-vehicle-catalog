import json

import pytest

from autohome_catalog.image_merge import IMAGE_FIELDS, canonical, check, guard, projected
from autohome_catalog.image_sync import assert_image_operation, url_lines


def test_json_and_legacy_urls_preserve_order_and_remove_markdown():
    urls = ["https://car2.autoimg.cn/2.jpg", "https://car2.autoimg.cn/1.jpg"]
    assert url_lines(json.dumps(urls)) == urls
    assert url_lines(json.dumps([f"[图]({u})" for u in urls])) == urls
    assert url_lines("\n".join(urls)) == urls
    assert url_lines("[]") == []


@pytest.mark.parametrize("value", ['["unfinished', "[1]", "[{}]", '["not a URL"]'])
def test_invalid_json_does_not_become_a_single_fake_url(value):
    with pytest.raises(ValueError):
        url_lines(value)


def test_projection_only_copies_image_fields_and_preserves_metadata():
    src = {
        "model_id": "AUT-1-BEV",
        "品牌": "old",
        "车型": "old name",
        "年款": "2026",
        "图片URL清单": '["https://car2.autoimg.cn/1.jpg"]',
        "图片数": 1,
        "说明": "source note",
        "来源页": "https://www.autohome.com.cn/",
        "检查时间": "then",
    }
    p = projected(src)
    assert set(p) == set(IMAGE_FIELDS.values())
    assert p["图片年款"] == "2026" and p["图片说明"] == "source note"
    assert json.loads(p["图片URL清单"]) == ["https://car2.autoimg.cn/1.jpg"]
    check(p, {**p, "图片类别": ""})
    check(p, {**p, "图片采集来源页": "[来源](https://www.autohome.com.cn/)"})
    assert canonical({"图片URL清单": "[]"})["图片年款"] == ""
    with pytest.raises(ValueError):
        projected({**src, "图片数": 2})
    with pytest.raises(ValueError):
        check(p, {**p, "图片URL清单": "[]"})


def test_merged_sync_and_merge_guard_reject_other_tables_and_model_creation():
    c = {
        "tables": {"models": "m", "trims": "t"},
        "image_storage": "models_json",
        "_image_merge": {"images_id": "i", "folder": "folder"},
    }
    assert_image_operation(c, "+record-batch-update", ["--table-id", "m"])
    guard(c, "+field-create", ["--table-id", "m"])
    for cmd, tid in [
        ("+record-batch-update", "i"),
        ("+record-batch-update", "t"),
        ("+record-batch-create", "m"),
        ("+table-create", "m"),
    ]:
        with pytest.raises(ValueError):
            assert_image_operation(c, cmd, ["--table-id", tid])
        with pytest.raises(ValueError):
            guard(c, cmd, ["--table-id", tid])


def test_existing_json_model_images_sync_without_writes(tmp_path, monkeypatch):
    import autohome_catalog.image_merge as module
    from autohome_catalog.images import fingerprint

    t = {
        "model_id": "AUT-1-BEV",
        "series_id": 1,
        "year": 2027,
        "name": "source name",
        "brand": "brand",
        "power": "BEV",
        "model_record": "old-parent",
        "specs": [1],
    }
    r = {
        "target": t,
        "target_sha256": fingerprint(t),
        "images": [],
        "status": "无匹配官图",
        "note": "none",
        "problems": [],
        "checked_at": "then",
    }
    expected = projected(module.fields(r))
    # The user deleted this column; an ordinary image sync must not restore or write it.
    expected.pop("图片检查结果")
    before = [
        {
            "record_id": "keep-id",
            "fields": {
                "model_id": t["model_id"],
                "车系名称": "current name",
                "配置版本": [{"id": "trim-id"}],
                **expected,
            },
        }
    ]
    monkeypatch.setattr(module, "read_records", lambda *args: before)
    monkeypatch.setattr(module, "all_results", lambda root: [r])

    class Client:
        config = {"tables": {"models": "models"}}

        def call(self, *args):
            raise AssertionError("No mutation is needed for an identical JSON list")

    result = module.sync_models(Client(), tmp_path)
    assert result["created"] == 0 and result["updated"] == 0
    assert before[0]["fields"]["车系名称"] == "current name"
    assert before[0]["fields"]["配置版本"] == [{"id": "trim-id"}]
