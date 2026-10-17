"""Unit tests simulate an OS mount boundary; they never need the user's disk name."""

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_test_volume(tmp_path, monkeypatch):
    actual_ismount = os.path.ismount
    monkeypatch.setenv("AUTOHOME_EXTERNAL_MOUNT", str(tmp_path.parent))
    monkeypatch.setattr(
        os.path,
        "ismount",
        lambda path: Path(path).resolve() == tmp_path.parent.resolve() or actual_ismount(path),
    )
