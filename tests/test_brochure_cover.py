import hashlib
import urllib.error
from copy import deepcopy

import pytest

from autohome_catalog.brochure_cover import (
    FIELD,
    attachment_matches,
    download,
    guard,
    image_extension,
    targets,
    unchanged,
)


def test_only_one_source_cover_and_no_other_identity_can_enter_targets():
    rows = [
        {
            "record_id": "record",
            "fields": {
                "model_id": "AUT-1-BEV",
                "来源车系ID": "1",
                "车系名称": "车",
                "车系图片URL": "[cover](https://car3.autoimg.cn/a.png)",
            },
        }
    ]
    assert targets(rows)["AUT-1-BEV"]["url"] == "https://car3.autoimg.cn/a.png"
    rows[0]["fields"]["车系图片URL"] = "https://other.test/a.png"
    with pytest.raises(ValueError):
        targets(rows)


def test_mime_and_signature_both_required():
    assert image_extension(b"\x89PNG\r\n\x1a\nfile", "image/png") == ".png"
    assert image_extension(b"\xff\xd8\xffbytes", "image/jpeg") == ".jpg"
    for body, mime in [(b"<html>blocked</html>", "image/png"), (b"\x89PNG\r\n\x1a\n", "text/html")]:
        with pytest.raises(ValueError):
            image_extension(body, mime)


def test_repeat_run_skips_matching_single_attachment_and_rejects_duplicates():
    target = {"model_id": "AUT-1-BEV"}
    receipt = {"sha256": "a" * 64, "extension": ".png", "size": 123}
    item = {"name": "AUT-1-BEV-aaaaaaaaaaaa.png", "size": 123, "file_token": "file"}
    assert attachment_matches([item], target, receipt)
    assert not attachment_matches([item, item], target, receipt)
    assert not attachment_matches([], target, receipt)
    assert not attachment_matches([{**item, "size": 124}], target, receipt)


def test_cover_does_not_change_other_model_fields():
    before = {"id": {"record_id": "record", "fields": {"model_id": "id", "图片数": 15}}}
    after = deepcopy(before)
    after["id"]["fields"][FIELD] = [{"file_token": "new"}]
    unchanged(before, after)
    after["id"]["fields"]["图片数"] = 1
    with pytest.raises(ValueError):
        unchanged(before, after)


def test_only_cover_attachment_operation_on_models_allowed():
    config = {"tables": {"models": "models"}, "_brochure_cover": {"field_id": "cover"}}
    guard(config, "+record-upload-attachment", ["--table-id", "models", "--field-id", "cover"])
    for command, tid in [
        ("+record-upload-attachment", "trims"),
        ("+record-batch-update", "models"),
    ]:
        with pytest.raises(ValueError):
            guard(config, command, ["--table-id", tid])


def test_source_403_pauses_and_never_retries_identity(tmp_path, monkeypatch):
    calls = []

    class Opener:
        def open(self, url, timeout):
            calls.append(url)
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(
        "autohome_catalog.brochure_cover.urllib.request.build_opener", lambda *_: Opener()
    )
    url = "https://car3.autoimg.cn/a.png"
    with pytest.raises(urllib.error.HTTPError):
        download(tmp_path, tmp_path, url)
    assert (tmp_path / "source-paused.json").exists()
    with pytest.raises(ValueError, match="paused"):
        download(tmp_path, tmp_path, url)
    assert calls == [url]


def test_download_cache_requires_matching_content_hash(tmp_path):
    from autohome_catalog.storage import save_json

    url = "https://car3.autoimg.cn/a.png"
    photo = tmp_path / "a.png"
    photo.write_bytes(b"original")
    receipt = {"url": url, "file": str(photo), "sha256": hashlib.sha256(b"original").hexdigest()}
    save_json(
        tmp_path / "downloads" / (hashlib.sha256(url.encode()).hexdigest() + ".json"), receipt
    )
    assert download(tmp_path, tmp_path, url) == receipt
    photo.write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash"):
        download(tmp_path, tmp_path, url)


def test_transient_transport_retries_same_url_without_identity_change(tmp_path, monkeypatch):
    from types import SimpleNamespace

    calls = []
    body = b"\x89PNG\r\n\x1a\nimage"

    class Response:
        status = 200
        headers = SimpleNamespace(get_content_type=lambda: "image/png")

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, size):
            return body

    class Opener:
        def open(self, url, timeout):
            calls.append(url)
            if len(calls) == 1:
                raise urllib.error.URLError("connection closed")
            return Response()

    monkeypatch.setattr(
        "autohome_catalog.brochure_cover.urllib.request.build_opener", lambda *_: Opener()
    )
    monkeypatch.setattr("autohome_catalog.brochure_cover.time.sleep", lambda _: None)
    url = "https://car3.autoimg.cn/a.png"
    result = download(tmp_path, tmp_path, url)
    assert result["sha256"] == hashlib.sha256(body).hexdigest()
    assert calls == [url, url]
