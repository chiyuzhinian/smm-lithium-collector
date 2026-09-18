"""completeness — Anchor products + Rolling baseline 测试（Phase E1）。"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from smm_collector.completeness import (
    ANCHOR_PRODUCTS,
    compute_baseline,
    check_anchor_products,
    fetch_rows_per_day,
)


# ── check_anchor_products ─────────────────────────────────


def test_check_anchor_all_present():
    rows = [
        {"category": c[0], "product_name": c[1], "specification": c[2]}
        for c in ANCHOR_PRODUCTS
    ]
    r = check_anchor_products(rows)
    assert r.is_ok
    assert r.ratio == 1.0
    assert r.missing == []


def test_check_anchor_missing_some():
    rows = [
        {"category": c[0], "product_name": c[1], "specification": c[2]}
        for c in ANCHOR_PRODUCTS[:2]
    ]
    r = check_anchor_products(rows)
    assert not r.is_ok
    assert len(r.missing) == 3
    assert r.ratio == pytest.approx(0.4)


def test_check_anchor_empty_rows():
    r = check_anchor_products([])
    assert not r.is_ok
    assert r.ratio == 0.0
    assert len(r.missing) == len(ANCHOR_PRODUCTS)


def test_check_anchor_partial_match():
    """product_name 模糊但 specification 不匹配 → 不算命中。"""
    rows = [
        {"category": "碳酸锂", "product_name": "电池级碳酸锂",
         "specification": "WRONG_SPEC"},  # 不命中
    ]
    r = check_anchor_products(rows)
    assert not r.is_ok
    assert r.missing == list(ANCHOR_PRODUCTS)


def test_check_anchor_extra_rows_dont_affect():
    rows = [
        {"category": "X", "product_name": "Y", "specification": "Z"},
        *[{"category": c[0], "product_name": c[1], "specification": c[2]}
          for c in ANCHOR_PRODUCTS],
        {"category": "A", "product_name": "B", "specification": "C"},
    ]
    r = check_anchor_products(rows)
    assert r.is_ok


def test_check_anchor_custom_anchors():
    anchors = (("Foo", "Bar", "Baz"),)
    rows = [{"category": "Foo", "product_name": "Bar", "specification": "Baz"}]
    r = check_anchor_products(rows, anchors)
    assert r.is_ok


# ── compute_baseline ──────────────────────────────────────


def test_baseline_ok_in_range():
    history = [400, 420, 410, 430, 415, 405, 425, 415, 405, 415]
    r = compute_baseline(history, today_rows=412)
    assert r.status == "ok"
    assert r.median > 0
    assert r.p20 <= r.median <= r.p80


def test_baseline_warn_short():
    history = [400, 420, 410, 430, 415, 405, 425, 415, 405, 415]
    r = compute_baseline(history, today_rows=50)  # 远低于 p20 * 0.5
    assert r.status == "warn_short"


def test_baseline_warn_long():
    history = [400, 420, 410, 430, 415, 405, 425, 415, 405, 415]
    r = compute_baseline(history, today_rows=5000)  # 远超 p80 * 2
    assert r.status == "warn_long"


def test_baseline_insufficient_history():
    r = compute_baseline([100, 200, 300], today_rows=200, min_history=5)
    assert r.status == "unknown"
    assert "below" in r.detail or "unavailable" in r.detail


def test_baseline_empty_history():
    r = compute_baseline([], today_rows=100, min_history=5)
    assert r.status == "unknown"


def test_baseline_zero_today_still_warns():
    """0 行 → 必 warn_short（说明解析失败/登录态失效）。"""
    history = [400, 420, 410, 430, 415, 405, 425, 415, 405, 415]
    r = compute_baseline(history, today_rows=0)
    assert r.status == "warn_short"


# ── fetch_rows_per_day ────────────────────────────────────


@pytest.fixture
def db_with_history(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("""
            CREATE TABLE lithium_spot_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                price_date TEXT, collected_at TEXT,
                category TEXT, product_name TEXT, specification TEXT,
                average_price REAL
            )
        """)
        # 写入 5 天数据，每天 100 行；旧日期算入回溯
        today = datetime.now().date()
        for days_ago in (1, 2, 3, 5, 7):
            d = today - timedelta(days=days_ago)
            for _ in range(100 if days_ago != 5 else 50):
                conn.execute(
                    "INSERT INTO lithium_spot_prices (price_date, collected_at, average_price) VALUES (?, ?, ?)",
                    (d.isoformat(), f"{d.isoformat()}T09:05:00", 100.0),
                )
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_fetch_rows_per_day_basic(db_with_history):
    rows = fetch_rows_per_day(db_with_history, lookback_days=14)
    # 应至少有 4-5 天数据（按日期聚合）
    assert len(rows) >= 3
    assert all(r > 0 for r in rows)


def test_fetch_rows_per_day_no_db(tmp_path):
    rows = fetch_rows_per_day(tmp_path / "missing.db")
    assert rows == []


def test_fetch_rows_per_day_end_date_filter(db_with_history):
    today = datetime.now().date()
    rows = fetch_rows_per_day(db_with_history, lookback_days=3, end_date=today)
    # 仅含今天前 3 天内
    assert all(r > 0 for r in rows)
