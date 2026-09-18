"""collector_status.json 读写测试（Phase E2）。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from smm_collector import collector_status


@pytest.fixture
def tmp_path_status(tmp_path: Path) -> Path:
    return tmp_path / "collector_status.json"


def test_read_returns_default_when_missing(tmp_path):
    p = collector_status.read(tmp_path / "missing.json")
    assert p["status"] == "never_run"
    assert p["consecutive_failures"] == 0


def test_read_returns_default_when_corrupted(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json")
    assert collector_status.read(path)["status"] == "never_run"


def test_atomic_write_creates_file(tmp_path_status):
    collector_status.atomic_write(tmp_path_status, collector_status._default_payload())
    assert tmp_path_status.exists()


def test_atomic_write_no_leftover_tmp(tmp_path_status):
    collector_status.atomic_write(tmp_path_status, collector_status._default_payload())
    leftovers = list(tmp_path_status.parent.glob(".collector_status.*.tmp"))
    assert leftovers == []


def test_record_run_success(tmp_path_status):
    now = datetime(2026, 9, 17, 9, 5, 12)
    collector_status.record_run(
        tmp_path_status,
        status="success",
        target_date="2026-09-17",
        data_date="2026-09-16",
        parsed_rows=438,
        validated_rows=438,
        inserted_rows=10,
        updated_rows=20,
        duplicate_rows=408,
        run_id="test-run-001",
        now=now,
    )
    p = collector_status.read(tmp_path_status)
    assert p["status"] == "success"
    assert p["consecutive_failures"] == 0
    assert p["last_success_at"] is not None
    assert p["last_error_type"] is None
    assert p["parsed_rows"] == 438
    assert p["inserted_rows"] == 10


def test_record_run_failure_increments_consecutive(tmp_path_status):
    collector_status.record_run(tmp_path_status, status="failed", error_type="ZERO_ROWS",
                                  error_message="parsed_rows=0")
    collector_status.record_run(tmp_path_status, status="failed", error_type="AUTH_EXPIRED",
                                  error_message="login gone")
    p = collector_status.read(tmp_path_status)
    assert p["consecutive_failures"] == 2
    assert p["last_error_type"] == "AUTH_EXPIRED"


def test_record_run_success_clears_errors(tmp_path_status):
    collector_status.record_run(tmp_path_status, status="failed",
                                  error_type="AUTH_EXPIRED", error_message="x")
    collector_status.record_run(tmp_path_status, status="success",
                                  target_date="2026-09-17", data_date="2026-09-16",
                                  parsed_rows=438, validated_rows=438,
                                  inserted_rows=10, duplicate_rows=428)
    p = collector_status.read(tmp_path_status)
    assert p["status"] == "success"
    assert p["last_error_type"] is None
    assert p["last_error_message"] is None
    assert p["consecutive_failures"] == 0


def test_record_run_invalid_status_falls_back_to_failed(tmp_path_status):
    collector_status.record_run(tmp_path_status, status="bogus")
    assert collector_status.read(tmp_path_status)["status"] == "failed"


def test_record_run_partial_success(tmp_path_status):
    collector_status.record_run(
        tmp_path_status, status="partial_success",
        target_date="2026-09-17", parsed_rows=200, validated_rows=200,
    )
    p = collector_status.read(tmp_path_status)
    assert p["status"] == "partial_success"
    # partial_success 视为成功（不递增失败）
    assert p["consecutive_failures"] == 0


def test_record_run_staleness_fields(tmp_path_status):
    collector_status.record_run(
        tmp_path_status, status="success",
        staleness_status="ok",
        staleness_detail="today succeeded",
    )
    p = collector_status.read(tmp_path_status)
    assert p["staleness_status"] == "ok"
    assert p["staleness_detail"] == "today succeeded"


def test_record_run_next_scheduled(tmp_path_status):
    now = datetime(2026, 9, 17, 9, 5)
    nxt = datetime(2026, 9, 17, 9, 30)
    collector_status.record_run(tmp_path_status, status="failed",
                                  next_scheduled_at=nxt, now=now)
    p = collector_status.read(tmp_path_status)
    assert "2026-09-17" in p["next_scheduled_at"]


def test_record_run_long_error_message_truncated(tmp_path_status):
    long_msg = "x" * 1000
    collector_status.record_run(tmp_path_status, status="failed",
                                  error_type="X", error_message=long_msg)
    p = collector_status.read(tmp_path_status)
    assert len(p["last_error_message"]) <= 500
