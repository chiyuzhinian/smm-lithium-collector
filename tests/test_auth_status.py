"""auth_status.json 读写测试（Phase E2）。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from smm_collector import auth_status
from smm_collector.auth_health import AuthStatus


@pytest.fixture
def tmp_status_path(tmp_path: Path) -> Path:
    return tmp_path / "auth_status.json"


def test_read_returns_default_when_missing(tmp_path):
    p = auth_status.read(tmp_path / "missing.json")
    assert p["status"] == "AUTH_UNKNOWN"
    assert p["consecutive_failures"] == 0


def test_read_returns_default_when_corrupted(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json")
    assert auth_status.read(path)["status"] == "AUTH_UNKNOWN"


def test_read_fills_missing_keys(tmp_status_path):
    tmp_status_path.write_text(json.dumps({"status": "AUTH_OK"}))
    p = auth_status.read(tmp_status_path)
    assert "reason" in p
    assert "consecutive_failures" in p


def test_atomic_write_creates_file(tmp_status_path):
    auth_status.atomic_write(tmp_status_path, auth_status._default_payload())
    assert tmp_status_path.exists()


def test_atomic_write_no_leftover_tmp(tmp_status_path):
    auth_status.atomic_write(tmp_status_path, auth_status._default_payload())
    leftovers = list(tmp_status_path.parent.glob(".auth_status.*.tmp"))
    assert leftovers == []


def test_update_basic(tmp_status_path):
    auth_status.update(
        tmp_status_path,
        status=AuthStatus.OK,
        reason="check passed",
        last_check_at=datetime(2026, 9, 17, 9, 5, 12),
    )
    p = auth_status.read(tmp_status_path)
    assert p["status"] == "AUTH_OK"
    assert "2026-09-17" in p["last_check_at"]


def test_update_failure_increments_consecutive(tmp_status_path):
    auth_status.update(tmp_status_path, status=AuthStatus.EXPIRED, reason="x")
    auth_status.update(tmp_status_path, status=AuthStatus.EXPIRED, reason="y")
    assert auth_status.read(tmp_status_path)["consecutive_failures"] == 2


def test_update_success_resets_consecutive(tmp_status_path):
    auth_status.update(tmp_status_path, status=AuthStatus.EXPIRED, reason="x")
    auth_status.update(tmp_status_path, status=AuthStatus.EXPIRED, reason="y")
    auth_status.update(tmp_status_path, status=AuthStatus.OK, reason="ok")
    assert auth_status.read(tmp_status_path)["consecutive_failures"] == 0


def test_update_success_clears_failure_reason(tmp_status_path):
    auth_status.update(tmp_status_path, status=AuthStatus.EXPIRED,
                       reason="x", failure_reason="cookie gone")
    auth_status.update(tmp_status_path, status=AuthStatus.OK, reason="ok")
    assert auth_status.read(tmp_status_path)["failure_reason"] is None


def test_update_auto_login_attempts_clamps_negative(tmp_status_path):
    auth_status.update(tmp_status_path, auto_login_attempts=-3)
    assert auth_status.read(tmp_status_path)["auto_login_attempts"] == 0


def test_update_only_allowed_keys(tmp_status_path):
    auth_status.update(tmp_status_path, status=AuthStatus.OK, reason="x",
                       profile_dir="/var/lib/smm-collector/browser-profile")
    p = auth_status.read(tmp_status_path)
    for k in p:
        assert k in auth_status.ALLOWED_KEYS


def test_update_string_status_accepted(tmp_status_path):
    auth_status.update(tmp_status_path, status="AUTH_OK", reason="x")
    assert auth_status.read(tmp_status_path)["status"] == "AUTH_OK"


def test_update_creates_parent_dirs(tmp_path):
    nested = tmp_path / "a" / "b" / "c" / "auth.json"
    auth_status.update(nested, status=AuthStatus.OK, reason="x")
    assert nested.exists()
