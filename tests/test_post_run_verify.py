"""post_run_verify — 采集后验证测试（Phase E1）。"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from smm_collector.post_run_verify import (
    verify_max_price_date,
    verify_no_price_wall,
    verify_recent_collected_at,
    verify_row_counts,
)


# ── verify_row_counts ─────────────────────────────────────


def test_verify_row_counts_ok():
    r = verify_row_counts(parsed_rows=438, validated_rows=438,
                          inserted=10, updated=20, duplicate=408)
    assert r.ok
    assert r.error_type is None
    assert r.checks["row_count_parsed"]["ok"] is True


def test_verify_zero_parsed_fails():
    r = verify_row_counts(parsed_rows=0, validated_rows=0,
                          inserted=0, updated=0, duplicate=0)
    assert not r.ok
    assert r.error_type == "ZERO_ROWS"


def test_verify_too_many_invalid_fails():
    """validated < 95% of parsed → VALIDATION_FAILED。"""
    r = verify_row_counts(parsed_rows=100, validated_rows=80,
                          inserted=10, updated=20, duplicate=50)
    assert not r.ok
    assert r.error_type == "VALIDATION_FAILED"


def test_verify_accounted_mismatch_fails():
    """inserted+updated+duplicate ≠ validated → VALIDATION_FAILED。"""
    r = verify_row_counts(parsed_rows=100, validated_rows=100,
                          inserted=10, updated=10, duplicate=10)
    assert not r.ok
    assert r.error_type == "VALIDATION_FAILED"


def test_verify_accounted_tolerates_small_drift():
    """accounted 与 validated 差距 < 5% → ok。"""
    r = verify_row_counts(parsed_rows=100, validated_rows=100,
                          inserted=10, updated=20, duplicate=72)  # 102 vs 100
    assert r.ok


# ── verify_max_price_date ─────────────────────────────────


@pytest.fixture
def db_with_data(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("""
            CREATE TABLE lithium_spot_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                price_date TEXT, collected_at TEXT,
                category TEXT, average_price REAL
            )
        """)
        # 写入 5 行
        for i in range(5):
            conn.execute(
                "INSERT INTO lithium_spot_prices (price_date, collected_at, average_price) VALUES (?, ?, ?)",
                ("2026-09-16", "2026-09-17T09:05:00", 100.0 + i),
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_verify_max_price_date_ok(db_with_data):
    r = verify_max_price_date(db_with_data, expected_data_date=date(2026, 9, 16))
    assert r.ok


def test_verify_max_price_date_tolerance(db_with_data):
    """expected 在 ±1 天内 → ok。"""
    r = verify_max_price_date(db_with_data, expected_data_date=date(2026, 9, 17))
    assert r.ok


def test_verify_max_price_date_out_of_range(db_with_data):
    r = verify_max_price_date(db_with_data, expected_data_date=date(2026, 9, 1))
    assert not r.ok
    assert r.error_type == "POST_RUN_VERIFY_FAILED"


def test_verify_max_price_date_no_db(tmp_path):
    r = verify_max_price_date(tmp_path / "missing.db")
    assert not r.ok
    assert r.error_type == "DB_ERROR"


def test_verify_max_price_date_empty_db(tmp_path):
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("""
            CREATE TABLE lithium_spot_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                price_date TEXT, collected_at TEXT,
                average_price REAL
            )
        """)
        conn.commit()
    finally:
        conn.close()
    r = verify_max_price_date(db_path)
    assert not r.ok
    assert r.error_type == "ZERO_ROWS"


# ── verify_recent_collected_at ────────────────────────────


def test_verify_recent_collected_at_ok(db_with_data):
    r = verify_recent_collected_at(db_with_data, since_minutes=30,
                                    now=datetime(2026, 9, 17, 9, 10))
    assert r.ok


def test_verify_recent_collected_at_too_old(db_with_data):
    """collected_at 距 now 太远 → 失败。"""
    r = verify_recent_collected_at(db_with_data, since_minutes=10,
                                    now=datetime(2026, 12, 1, 0, 0))
    assert not r.ok
    assert r.error_type == "POST_RUN_VERIFY_FAILED"


def test_verify_recent_collected_at_no_db(tmp_path):
    r = verify_recent_collected_at(tmp_path / "missing.db")
    assert not r.ok
    assert r.error_type == "DB_ERROR"


# ── verify_no_price_wall ──────────────────────────────────


def test_verify_no_price_wall_normal_rows():
    rows = [{"average_price": 100}, {"average_price": 200}, {"average_price": 150}]
    r = verify_no_price_wall(rows)
    assert r.ok


def test_verify_no_price_wall_detected():
    """多数行均价空 → PRICE_WALL。"""
    rows = [{"average_price": None}] * 9 + [{"average_price": 100}]
    r = verify_no_price_wall(rows)
    assert not r.ok
    assert r.error_type == "PRICE_WALL"


def test_verify_no_price_wall_below_min_rows():
    rows = [{"average_price": None}] * 3
    r = verify_no_price_wall(rows, min_rows=10)
    assert r.ok  # 行数太少，不触发


def test_verify_no_price_wall_empty():
    r = verify_no_price_wall([])
    assert r.ok
