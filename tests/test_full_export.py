import csv
import hashlib
import json

import pytest
from test_export import packet

from autohome_catalog.full_export import export_full


def test_full_csv_preserves_unknown_fields_conditions_and_source_identity(tmp_path):
    p = packet()
    p["payload"]["titlelist"][0]["items"].append(
        {"itemid": 99999, "titleid": 88, "itemname": "全量新增项[条件]"}
    )
    p["payload"]["datalist"][0]["paramconflist"].append({"titleid": 88, "itemname": "前●/后-"})
    p["payload"]["datalist"][0]["paramconflist"][0]["itemname"] = "4800-4900"
    raw = json.dumps({"result": p["payload"]}).encode()
    (tmp_path / "parameters.raw").write_bytes(raw)
    source = {
        **p["parameter_source"],
        "file": "parameters.raw",
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    p["parameter_source"] = source
    p["spec_list_source"] = source
    p["series"]["catalog_source"] = source
    (tmp_path / "series").mkdir()
    (tmp_path / "series/123.json").write_text(json.dumps(p))
    (tmp_path / "status.json").write_text(
        json.dumps({"phase": "complete", "completed_series": [123], "missing_series": []})
    )
    (tmp_path / "scope-manifest.json").write_text(
        json.dumps(
            {
                "counts": {"2": 1, "3": 0},
                "rows": [{"seriesid": 123, "specids": [81, 82], "scope_status": "在售"}],
            }
        )
    )
    result = export_full(tmp_path)
    assert result["models"] == 1 and result["trims"] == 2 and result["new_fields"] == 1
    with (tmp_path / "完整配置.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["长度(mm)"] == "4800-4900"
    assert rows[0]["全量新增项（条件）"] == "前标配（●）/后-"
    assert rows[0]["来源快照SHA256"] == source["sha256"]
    assert rows[1]["全量新增项（条件）"] == ""
    p["payload"]["datalist"][0]["paramconflist"][0]["itemname"] = "伪造"
    (tmp_path / "series/123.json").write_text(json.dumps(p))
    with pytest.raises(ValueError, match="captured"):
        export_full(tmp_path)
