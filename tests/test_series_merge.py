from copy import deepcopy

import pytest

from autohome_catalog.series_merge import compile_models, guard, remap_links


def sample():
    models, trims = [], []
    for code, power in [("BEV", "纯电动"), ("PHEV", "插电式混合动力")]:
        mid = f"AUT-1-{code}"
        models.append(
            {
                "record_id": code,
                "fields": {
                    "model_id": mid,
                    "来源车系ID": "1",
                    "原车系名称": "秦PLUS",
                    "动力类型": power,
                    "配置版本": [{"id": "trim-" + code}],
                    "图片URL清单（json字符串）": '["https://car3.autoimg.cn/shared.jpg"]',
                    "图片数": 1,
                    "图片年款": "2026",
                    "brochure_cover": [{"name": mid + "-hash.png", "size": 12}],
                },
            }
        )
        trims.append(
            {
                "record_id": "trim-" + code,
                "fields": {
                    "model_id": mid,
                    "来源车系ID": "1",
                    "所属车型": [{"id": code}],
                },
            }
        )
    return models, trims


def test_merge_preserves_powertrain_image_groups_and_unique_series_cover():
    models, trims = sample()
    before = deepcopy((models, trims))
    patch, mapping = compile_models(models, trims)
    assert mapping == {"BEV": "BEV", "PHEV": "BEV"}
    assert patch["BEV"]["动力类型"] == ["BEV", "PHEV"]
    assert patch["BEV"]["车系名称"] == "秦PLUS"
    assert patch["BEV"]["图片数"] == 1
    assert '"PHEV"' in patch["BEV"]["图库分组JSON"]
    assert (models, trims) == before


@pytest.mark.parametrize("change", ["wrong-parent", "wrong-power", "wrong-cover", "missing-trim"])
def test_unsafe_merges_stop_before_writing(change):
    models, trims = sample()
    if change == "wrong-parent":
        trims[0]["fields"]["所属车型"] = [{"id": "PHEV"}]
    elif change == "wrong-power":
        models[0]["fields"]["动力类型"] = "增程式"
    elif change == "wrong-cover":
        models[1]["fields"]["brochure_cover"][0]["size"] = 999
    else:
        trims.pop()
    with pytest.raises(ValueError):
        compile_models(models, trims)


def test_many_old_links_collapse_only_to_the_resolved_survivor():
    assert remap_links([{"id": "a"}, {"id": "b"}], {"a": "a", "b": "a"}) == [{"id": "a"}]
    with pytest.raises(KeyError):
        remap_links([{"id": "other"}], {"a": "a"})


def test_deletion_guard_accepts_only_planned_duplicate_models():
    config = {
        "tables": {"models": "models"},
        "_series_merge": {
            "tables": ["models", "trims"],
            "delete_ids": ["duplicate"],
        },
    }
    guard(config, "+record-delete", ["--table-id", "models", "--record-id", "duplicate"])
    for table, rid in [("models", "survivor"), ("trims", "duplicate")]:
        with pytest.raises(ValueError):
            guard(config, "+record-delete", ["--table-id", table, "--record-id", rid])


def test_async_revision_change_restarts_only_the_read(monkeypatch):
    from autohome_catalog.series_merge import export_rows

    calls = []

    def read(*args):
        calls.append(args)
        if len(calls) == 1:
            raise ValueError("Table changed during snapshot: table")
        return [{"record_id": "one", "fields": {}}]

    monkeypatch.setattr("autohome_catalog.series_merge._export_rows_once", read)
    monkeypatch.setattr("autohome_catalog.series_merge.time.sleep", lambda _: None)
    assert export_rows(None, "table", None, "test")[0]["record_id"] == "one"
    assert len(calls) == 2
