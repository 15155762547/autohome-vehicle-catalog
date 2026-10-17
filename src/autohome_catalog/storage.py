"""External-volume-only artifacts and request guards. Never fetch media bytes."""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .models import SourceReceipt


def assert_external(path: Path, mount: Path | None = None) -> Path:
    mount = mount or Path(os.environ.get("AUTOHOME_EXTERNAL_MOUNT", "/Volumes/奥睿科1TB"))
    if not os.path.ismount(mount):
        raise ValueError(f"External volume is not mounted: {mount}")
    path, mount = path.resolve(), mount.resolve()
    if mount == Path("/") or path == mount or not path.is_relative_to(mount):
        raise ValueError(f"Data path must be inside the external volume: {path}")
    return path


def save_json(path: Path, value):
    assert_external(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".writing")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def assert_run_allowed(root: Path):
    if (
        (root / "PAUSED.json").exists()
        or (root / "STOP").exists()
        or (root.parent / "ACCESS_PAUSED.json").exists()
        or access_pause_path().exists()
    ):
        raise ValueError(
            "Run is paused; inspect the persistent pause before further source requests"
        )


def access_pause_path() -> Path:
    mount = Path(os.environ.get("AUTOHOME_EXTERNAL_MOUNT", "/Volumes/奥睿科1TB"))
    return mount / ".autohome-catalog-control" / "ACCESS_PAUSED.json"


def validate_request_url(url: str):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "www.autohome.com.cn":
        raise ValueError("Only the public Autohome source host is allowed")
    if re.search(r"\.(jpe?g|png|webp|gif|svg|mp4|mp3|avif)(?:$|/)", parsed.path, re.I):
        raise ValueError("No media downloads are permitted")


def guard_response(status: int, raw: bytes, content_type: str):
    if status in (401, 403, 429) or 300 <= status < 400:
        raise ValueError(f"Access paused: HTTP {status}")
    if status >= 400:
        raise ValueError(f"Source error: HTTP {status}")
    if not any(t in content_type.lower() for t in ("json", "text/", "xml")):
        raise ValueError(f"Unexpected binary content: {content_type}")
    # Match actual challenge page titles/forms, not news text mentioning verification.
    text = raw.decode("utf-8", errors="replace")
    if "json" in content_type.lower():
        try:
            envelope = json.loads(text)
        except ValueError:
            envelope = {}
        message = str(envelope.get("message", "")) if isinstance(envelope, dict) else ""
        if any(
            term in message
            for term in ("验证码", "安全验证", "访问过于频繁", "请求过于频繁", "访问受限")
        ):
            raise ValueError("Access paused: JSON challenge or access limit")
    if re.search(r"<title>[^<]*(安全验证|访问验证|验证码|Access Denied)", text, re.I):
        raise ValueError("Access paused: challenge page")
    if 'id="geetest' in text or 'id="captcha' in text:
        raise ValueError("Access paused: challenge form")


def capture(root: Path, response, request) -> dict:
    assert_external(root)
    raw = response.body
    digest = hashlib.sha256(raw).hexdigest()
    url_digest = hashlib.sha256(response.url.encode()).hexdigest()[:16]
    relative = f"raw/{url_digest}-{digest}.raw"
    file = root / relative
    file.parent.mkdir(parents=True, exist_ok=True)
    if file.exists() and file.read_bytes() != raw:
        raise ValueError("Raw artifact hash collision or corruption")
    file.write_bytes(raw)
    receipt = SourceReceipt(
        url=request.url,
        final_url=response.url,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        sha256=digest,
        file=relative,
        status=response.status,
        elapsed_seconds=float(request.meta.get("download_latency", 0)),
    ).model_dump()
    save_json(root / f"sources/{url_digest}-{digest}.json", receipt)
    return receipt
