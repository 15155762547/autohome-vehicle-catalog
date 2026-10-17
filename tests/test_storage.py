from pathlib import Path

import pytest

from autohome_catalog.storage import assert_external, guard_response, validate_request_url


def test_missing_mount_and_path_outside_mount_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="mounted"):
        assert_external(tmp_path / "new", tmp_path / "missing-volume")
    with pytest.raises(ValueError, match="inside"):
        assert_external(Path("/outside"), Path("/"))


def test_image_requests_are_never_allowed():
    with pytest.raises(ValueError, match="source host"):
        validate_request_url("https://car2.autoimg.cn/example.jpg")
    with pytest.raises(ValueError, match="media"):
        validate_request_url("https://www.autohome.com.cn/example.jpg")
    validate_request_url("https://www.autohome.com.cn/web-main/car/param/getParamConf?seriesid=1")


def test_access_limit_redirect_captcha_and_binary_are_rejected():
    for status in [401, 403, 429, 307]:
        with pytest.raises(ValueError, match="HTTP"):
            guard_response(status, b"", "text/html")
    with pytest.raises(ValueError, match="challenge"):
        guard_response(200, "<title>安全验证</title>".encode(), "text/html")
    with pytest.raises(ValueError, match="binary"):
        guard_response(200, b"image", "image/jpeg")
    guard_response(200, b'{"returncode":0}', "application/json")


def test_middleware_capture_accepts_response_before_request_is_attached(tmp_path):
    from scrapy.http import Request, TextResponse

    from autohome_catalog.storage import capture

    request = Request("https://www.autohome.com.cn/robots.txt")
    response = TextResponse(request.url, body=b"User-agent: *", encoding="utf8")
    assert response.request is None
    receipt = capture(tmp_path, response, request)
    assert receipt["url"] == request.url
    assert (tmp_path / receipt["file"]).read_bytes() == response.body


def test_access_pause_applies_to_new_run_directories(tmp_path):
    from autohome_catalog.storage import assert_run_allowed

    (tmp_path / "ACCESS_PAUSED.json").write_text("{}")
    with pytest.raises(ValueError, match="pause"):
        assert_run_allowed(tmp_path / "a-new-run")


def test_pause_is_shared_across_different_data_roots(tmp_path, monkeypatch):
    from autohome_catalog.storage import access_pause_path, assert_run_allowed

    monkeypatch.setenv("AUTOHOME_EXTERNAL_MOUNT", str(tmp_path))
    marker = access_pause_path()
    marker.parent.mkdir()
    marker.write_text("{}")
    for root in [tmp_path / "data-a" / "run", tmp_path / "data-b" / "run"]:
        with pytest.raises(ValueError, match="pause"):
            assert_run_allowed(root)


def test_json_access_limit_is_not_treated_as_normal_business_error():
    with pytest.raises(ValueError, match="challenge"):
        guard_response(
            200,
            '{"returncode":-1,"message":"访问过于频繁，请稍后重试"}'.encode(),
            "application/json",
        )
