import copy

import pytest

from autohome_catalog.legacy.model_grain import build_plan, guard


def dataset():
    models = [
        {
            "record_id": "series1",
            "fields": {
                "来源车系ID": "1",
                "车系名称": "秦PLUS",
                "品牌": "比亚迪",
                "model_id": "AUT-1-BEV\nAUT-1-PHEV",
                "配置版本": [{"id": "trim1"}, {"id": "trim2"}],
            },
        }
    ]
    trims = [
        {
            "record_id": f"trim{n}",
            "fields": {
                "来源配置ID": str(n),
                "来源车系ID": "1",
                "model_id": mid,
                "能源类型": power,
                "所属车系": [{"id": "series1"}],
            },
        }
        for n, mid, power in [(1, "AUT-1-BEV", "纯电动"), (2, "AUT-1-PHEV", "插电式混合动力")]
    ]
    images = [
        {
            "record_id": f"image{n}",
            "fields": {"model_id": t["fields"]["model_id"], "所属车系": [{"id": "series1"}]},
        }
        for n, t in enumerate(trims, 1)
    ]
    return {
        "models": models,
        "trims": trims,
        "images": images,
        "equipment": [{"record_id": "ex1", "fields": {"对应配置": [{"id": "trim1"}]}}],
        "special": [{"record_id": "sp1", "fields": {"对应配置": [{"id": "trim2"}]}}],
    }


def schema():
    return [{"name": n, "type": "text"} for n in ["model_id", "车系名称", "品牌", "来源车系ID"]] + [
        {"name": "配置版本", "type": "link"}
    ]


def test_split_distinct_names_ids_and_parent_lists():
    source = dataset()
    original = copy.deepcopy(source)
    plan = build_plan(source, schema())
    assert [r["fields"]["车系名称"] for r in plan["rows"]] == ["秦PLUS（BEV）", "秦PLUS（PHEV）"]
    assert [r["trim_ids"] for r in plan["rows"]] == [["trim1"], ["trim2"]]
    assert [r["image_record"] for r in plan["rows"]] == ["image1", "image2"]
    assert all(r["fields"]["原车系名称"] == "秦PLUS" for r in plan["rows"])
    assert source == original


@pytest.mark.parametrize("failure", ["image_model", "old_parent", "energy", "dependent"])
def test_preflight_rejects_ambiguous_or_wrong_links(failure):
    source = dataset()
    if failure == "image_model":
        source["images"][0]["fields"]["model_id"] = "AUT-1-EREV"
    elif failure == "old_parent":
        source["trims"][0]["fields"]["所属车系"] = [{"id": "wrong"}]
    elif failure == "energy":
        source["trims"][0]["fields"]["能源类型"] = "增程式"
    else:
        source["equipment"][0]["fields"]["对应配置"] = [{"id": "missing"}]
    with pytest.raises(ValueError):
        build_plan(source, schema())


def test_migration_cannot_write_archived_rows_or_parameter_extensions():
    config = {
        "tables": {"models": "old", "trims": "trim", "images": "img", "equipment": "ext"},
        "_model_grain_operation": {"old_models": "old", "new_models": "new", "folder": "folder"},
    }
    guard(config, "+record-batch-create", ["--table-id", "new"])
    guard(config, "+record-batch-update", ["--table-id", "trim"])
    for cmd, args in [
        ("+record-batch-update", ["--table-id", "old"]),
        ("+record-batch-update", ["--table-id", "ext"]),
        ("+field-delete", ["--table-id", "trim"]),
        ("+record-batch-create", ["--table-id", "trim"]),
    ]:
        with pytest.raises(ValueError):
            guard(config, cmd, args)


def test_client_uses_unique_model_id_when_series_ids_repeat():
    from autohome_catalog.feishu import Client

    client = object.__new__(Client)
    client.config = {"tables": {"models": "new"}, "key_fields": {"models": "model_id"}}
    client.call = lambda *args: {
        "rev": 1,
        "has_more": False,
        "fields": ["model_id", "来源车系ID"],
        "record_id_list": ["bev", "phev"],
        "data": [["AUT-1-BEV", "1"], ["AUT-1-PHEV", "1"]],
    }
    assert len(client.records("models")) == 2


def test_reverse_repair_writes_complete_membership(monkeypatch, tmp_path):
    import autohome_catalog.legacy.model_grain as migration

    expected = [{"id": rid} for rid in ["trim1", "trim2", "trim3"]]
    current = [
        {
            "record_id": "model1",
            "fields": {
                "model_id": "AUT-1-PHEV",
                "配置版本": expected[-1:],
                "图片组": [{"id": "image1"}],
            },
        }
    ]
    monkeypatch.setattr(migration, "table_rows", lambda *args: current)
    writes = []
    monkeypatch.setattr(migration, "update_links", lambda *args: writes.append(args[-1]))
    plan = {
        "new_models": 1,
        "rows": [
            {"key": "AUT-1-PHEV", "trim_ids": ["trim1", "trim2", "trim3"], "image_record": "image1"}
        ],
    }
    migration.reconcile_reverse(None, tmp_path, plan, {"new_models": "new"})
    assert writes == [{"model1": {"配置版本": expected}}]
    current[0]["fields"]["配置版本"] = expected
    migration.reconcile_reverse(None, tmp_path, plan, {"new_models": "new"})
    assert writes[-1] == {}
