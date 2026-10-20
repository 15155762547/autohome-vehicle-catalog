import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from autohome_catalog.color_sync import (
    capacity_error,
    guard,
    inspect_html,
    patch_index,
    read,
    schema,
    verify_detail,
)
from autohome_catalog.colors import (
    AVAILABILITY,
    KEY,
    RIGHT,
    SourceColor,
    classify,
    compile_colors,
    gallery_identity,
    source_list,
    stable_id,
)


def item(
    name="快银", value="#BAB8C3", price=12000, link="https://car.autohome.com.cn/pic/spec-1-2.html"
):
    return {
        "name": name,
        "value": value,
        "price": price,
        "picnum": 3 if link else 0,
        "linkurl": link,
    }


def source(exterior, interior=()):
    fields = {
        "车型全称": "品牌 车型 2027款",
        "model_id": "AUT-1-BEV",
        "品牌": "品牌",
        "车系名称": "车型",
        "配置名称": "2027款",
        "来源配置ID": "1",
    }
    parent = {"1": {"record_id": "parent", "fields": fields}}
    raw = {
        side: "颜色：" + json.dumps({"type": n, "list": list(values)}, ensure_ascii=False)
        for side, n, values in [("外观颜色", 1, exterior), ("内饰颜色", 2, interior)]
    }
    row = {
        "1": {
            "record_id": "old",
            "fields": {
                **fields,
                **raw,
                "对应配置": [{"id": "parent"}],
                "颜色原文JSON": json.dumps(raw, ensure_ascii=False),
            },
        }
    }
    return row, parent


def test_compile_keeps_price_zero_unknown_and_each_source_item():
    src, index = source(
        [item(), item("星空灰", "#4C4C51", 0, None)],
        [
            item(
                "黑色/浅灰色",
                "#000000/#DCDCDC",
                8000,
                "https://car.autohome.com.cn/pic/spec-1-I3.html",
            )
        ],
    )
    out, statuses, counts = compile_colors(src, index)
    assert len(out) == 3 and statuses == {"1": AVAILABILITY[0]}
    assert counts["有图库"] == 2 and counts["无图库"] == 1
    zero = next(f for f in out.values() if f["原厂色名"] == "星空灰")
    assert zero["加价（元）"] is None and zero["价格说明"] == ["未列加价"]
    assert json.loads(zero["单项原文JSON"])["price"] == 0
    interior = out["1:内饰:I3"]
    assert interior["基础色"] == ["黑", "灰"]
    assert interior["单色／双拼"] == ["双拼"]
    assert interior["对应配置"] == [{"id": "parent"}]
    assert list(out.values())[0]["原始顺序"] == 1
    assert list(out.values())[1]["原始顺序"] == 2


def test_empty_lists_create_no_fake_color_rows():
    out, statuses, _ = compile_colors(*source([]))
    assert out == {} and statuses == {"1": AVAILABILITY[3]}
    for ext, inner, status in [
        ([item()], [], AVAILABILITY[1]),
        ([], [item(link="https://car.autohome.com.cn/pic/spec-1-I3.html")], AVAILABILITY[2]),
    ]:
        assert compile_colors(*source(ext, inner))[1]["1"] == status


def test_source_snapshot_reconciliation_and_parent_identity():
    src, index = source([item()])
    # The old whole-array backup contains escaped quotes in its JSON string.
    raw = json.loads(src["1"]["fields"]["颜色原文JSON"])
    raw["外观颜色"] = raw["外观颜色"].replace('"', '\\"')
    src["1"]["fields"]["颜色原文JSON"] = json.dumps(raw)
    assert len(compile_colors(src, index)[0]) == 1
    src["1"]["fields"]["外观颜色"] = src["1"]["fields"]["外观颜色"].replace("12000", "8000")
    with pytest.raises(ValueError, match="Display/raw"):
        compile_colors(src, index)
    src, index = source([item()])
    index["1"]["fields"] = {**index["1"]["fields"], "model_id": "other"}
    with pytest.raises(ValueError, match="identity"):
        compile_colors(src, index)


def test_stable_ids_preserve_same_name_different_gallery_and_ordered_values():
    one = SourceColor(**item())
    two = one.model_copy(update={"linkurl": "https://car.autohome.com.cn/pic/spec-1-4.html"})
    assert stable_id("1", "外观", one) != stable_id("1", "外观", two)
    out, _, _ = compile_colors(*source([one.model_dump(), two.model_dump()]))
    assert len(out) == 2 and all(f["备注"] == "同名不同图库项目" for f in out.values())
    no_link = SourceColor(**item("Dual", "#000000/#FFFFFF", 0, None))
    assert stable_id("1", "外观", no_link) == stable_id(
        "1", "外观", no_link.model_copy(update={"price": 3})
    )
    assert stable_id("1", "外观", no_link) != stable_id(
        "1", "外观", no_link.model_copy(update={"value": "#FFFFFF/#000000"})
    )
    with pytest.raises(ValueError, match="collision"):
        compile_colors(*source([item(), item()]))


def test_classification_is_conservative_and_names_unchanged():
    assert classify("快银", "#BAB8C3") == (["银"], "名称与色值相符")
    assert classify("Ocean Blue", "#27364F") == (["蓝"], "名称与色值相符")
    assert classify("砂金石绿", "#666666") == ([], "待核对")
    assert classify("Mystery", "#000000") == ([], "待核对")
    assert classify("红色", "#0000FF") == ([], "待核对")
    assert source_list(None, "外观") == []
    with pytest.raises(ValueError):
        source_list('颜色：{"type":2,"list":[]}', "外观")
    with pytest.raises(ValueError):
        SourceColor(**item(value="red"))


@pytest.mark.parametrize(
    "url",
    [
        "https://car.autohome.com.cn/pic/spec-2-2.html",
        "https://car.autohome.com.cn/pic/spec-1-I2.html",
        "https://evil.test/pic/spec-1-2.html",
    ],
)
def test_exact_gallery_identity(url):
    with pytest.raises(ValueError):
        gallery_identity(url, "1", "外观")


def test_page_sample_checks_both_spec_and_color_metadata():
    html = b'<title>sample</title><script>{\\"CarSpecId\\":1,\\"innerColorId\\":3}</script>'
    assert inspect_html(html, "utf-8", "1", "内饰", "I3") == (True, "sample")
    assert not inspect_html(html, "utf-8", "1", "内饰", "I4")[0]
    assert not inspect_html(html, "utf-8", "2", "内饰", "I3")[0]


def test_detail_readback_and_schema_do_not_pack_or_restore_dimensions():
    out, _, _ = compile_colors(*source([item()]))
    rows = {k: {"fields": deepcopy(f)} for k, f in out.items()}
    verify_detail(out, rows)
    rows[next(iter(rows))]["fields"]["加价（元）"] = 1
    with pytest.raises(ValueError):
        verify_detail(out, rows)
    fields = schema("index", "reverse")
    assert [f["name"] for f in fields][:3] == ["颜色项目", "车型全称", "model_id"]
    assert [f["name"] for f in fields][-4:] == RIGHT
    assert next(f for f in fields if f["name"] == "加价（元）")["style"]["precision"] == 0


def test_migration_guard_and_index_patch_restrict_scope():
    config = {"_colors": {"tables": {"颜色": "owned"}, "index": "index", "source": "source"}}
    guard(config, "+record-batch-create", ["--table-id", "owned"])
    for command, tid in [("+record-batch-create", "source"), ("+record-batch-update", "models")]:
        with pytest.raises(ValueError):
            guard(config, command, ["--table-id", tid])
    with pytest.raises(ValueError):
        patch_index(None, "index", {"row": {"车型全称": "changed"}}, ["颜色资料"])
    assert capacity_error({"code": 1254103, "message": "RecordExceedLimit"})
    assert not capacity_error({"code": 1254104, "message": "RecordAddOnceExceedLimit"})
    assert not capacity_error({"message": "unauthorized"})


def test_color_read_pages_and_stable_key_validation():
    calls = []

    def call(command, args):
        offset = int(args[args.index("--offset") + 1])
        calls.append(offset)
        return {
            "rev": 1,
            "fields": [KEY],
            "record_id_list": [f"r{offset}"],
            "data": [[f"key{offset}"]],
            "has_more": offset == 0,
        }

    result = read(SimpleNamespace(call=call), "table", KEY)
    assert calls == [0, 1] and len(result) == 2


def test_stage_tolerates_native_option_colors_and_rerun_creates_no_duplicates(
    tmp_path, monkeypatch
):
    from autohome_catalog.color_sync import stage

    monkeypatch.setattr("autohome_catalog.color_sync.time.sleep", lambda _: None)
    expected, _, _ = compile_colors(*source([item(), item("灰", "#666666", 0, None)]))
    state = {"tables": {"颜色": "owned"}, "index": "index"}
    native = deepcopy(schema("index", "颜色（新入口）"))
    for field in native:
        if "options" in field:
            for option in field["options"]:
                option.update(hue="Blue", lightness="Lighter")
        if field["type"] == "number":
            field["style"].update(percentage=False, thousands_separator=False)
    rows, creates, updates = [], [], []
    views = {"index": ["车型全称", "颜色", "颜色（新入口）"], "owned": [f["name"] for f in native]}

    def call(command, args, body=None):
        tid = args[args.index("--table-id") + 1]
        if command == "+field-list":
            return {"fields": native}
        if command == "+view-list":
            return {"views": [{"id": "view", "type": "grid"}]}
        if command == "+view-get-visible-fields":
            return {"visible_fields": views[tid]}
        if command == "+view-set-visible-fields":
            views[tid] = body["visible_fields"]
            return {}
        if command == "+record-list":
            columns = list(expected[next(iter(expected))])
            return {
                "rev": 1,
                "fields": columns,
                "record_id_list": [r["record_id"] for r in rows],
                "data": [[r["fields"].get(c) for c in columns] for r in rows],
                "has_more": False,
            }
        if command == "+record-batch-update":
            updates.append(body["update_records"])
            for row in rows:
                row["fields"].update(body["update_records"].get(row["record_id"], {}))
            return {}
        if command == "+record-batch-create":
            creates.append(len(body["create_records"]))
            rows.extend(
                {"record_id": f"r{i}", "fields": deepcopy(f)}
                for i, f in enumerate(body["create_records"])
            )
            return {}
        raise AssertionError(command)

    client = SimpleNamespace(call=call)
    assert len(stage(client, tmp_path, state, "颜色", expected)) == 2
    assert len(stage(client, tmp_path, state, "颜色", expected)) == 2
    changed, _, _ = compile_colors(*source([item(price=13000), item("灰", "#666666", 0, None)]))
    stage(client, tmp_path, state, "颜色", changed)
    stage(client, tmp_path, state, "颜色", changed)
    assert creates == [2]
    assert len(updates) == 1
    assert set(next(iter(updates[0].values()))) == {"加价（元）", "单项原文JSON"}
    assert views["index"] == ["车型全称", "颜色"]
    assert views["owned"][-4:] == RIGHT
    assert KEY not in views["owned"]


def test_gallery_403_pauses_remaining_source_requests(tmp_path, monkeypatch):
    import urllib.error

    from autohome_catalog.color_sync import sample_links
    from autohome_catalog.storage import save_json

    expected, _, _ = compile_colors(
        *source([item(), item(link="https://car.autohome.com.cn/pic/spec-1-4.html")])
    )
    for f in expected.values():
        f["来源配置ID"] = "79396"
        f["图库入口"] = f["图库入口"].replace("spec-1-", "spec-79396-")
    save_json(tmp_path / "expected.json", expected)
    calls = []

    class Opener:
        def open(self, url, timeout):
            calls.append(url)
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(
        "autohome_catalog.color_sync.urllib.request.build_opener", lambda *_: Opener()
    )
    monkeypatch.setattr("autohome_catalog.color_sync.time.sleep", lambda _: None)
    samples = sample_links(tmp_path)
    assert len(calls) == len(samples) == 1
    assert next(iter(samples.values()))["result"] == "访问受阻"
    assert (tmp_path / "gallery-source-paused.json").exists()
    sample_links(tmp_path)
    assert len(calls) == 1


def test_numeric_maintenance_uses_color_item_key():
    from autohome_catalog.number_format import read_numbers

    def call(command, args):
        columns = [args[i + 1] for i, arg in enumerate(args) if arg == "--field-id"]
        assert columns[0] == KEY
        return {
            "rev": 1,
            "fields": columns,
            "record_id_list": ["r1", "r2"],
            "data": [["1:外观:2", 100], ["1:外观:3", 200]],
            "has_more": False,
        }

    client = SimpleNamespace(
        config={"tables": {"颜色": "table"}, "color_tables": {"颜色": "table"}}, call=call
    )
    assert len(read_numbers(client, "颜色", [{"id": "amount"}])) == 2


def test_same_name_different_hex_stays_two_items_with_explanation():
    out, _, _ = compile_colors(
        *source([item("米色", "#D1CBC6", 0, None), item("米色", "#C7C7BF", 0, None)])
    )
    assert len(out) == 2
    assert all(f["备注"] == "同名不同色值项目" for f in out.values())


def test_native_null_empty_list_is_unchanged_not_a_write():
    from autohome_catalog.color_sync import same_cell

    assert same_cell([], None)
    assert same_cell([], [])
    assert not same_cell(["红"], None)
    assert not same_cell(False, None)


def test_real_cli_capacity_response_is_a_table_limit_not_a_batch_limit():
    assert capacity_error(
        {
            "code": 800040832,
            "subtype": "quota_exceeded",
            "message": "conflict: The target table has reached the record limit.",
        }
    )


def test_gallery_redirect_does_not_follow_another_identity():
    import urllib.request

    from autohome_catalog.color_sync import GalleryRedirect

    request = urllib.request.Request("https://car.autohome.com.cn/pic/spec-1-2.html")
    for target in ["https://evil.test", "https://car.autohome.com.cn/pic/spec-1-3.html"]:
        with pytest.raises(ValueError, match="outside exact"):
            GalleryRedirect().redirect_request(request, None, 302, "redirect", {}, target)


def test_series_layout_uses_source_series_id_without_restoring_powertrain_id():
    src, index = source([item()])
    for row in [src["1"], index["1"]]:
        row["fields"].pop("model_id")
        row["fields"]["来源车系ID"] = "8757"
    out, _, _ = compile_colors(src, index)
    assert all(f["来源车系ID"] == "8757" and "model_id" not in f for f in out.values())
    src["1"]["fields"]["来源车系ID"] = "other"
    with pytest.raises(ValueError, match="identity"):
        compile_colors(src, index)
