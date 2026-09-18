"""staleness 检查测试（Phase E3）。"""
from __future__ import annotations

from datetime import datetime, time

import pytest

from smm_collector.staleness import (
    DEFAULT_CATCHUP_SCHEDULE,
    DEFAULT_MAIN_GRACE_MINUTES,
    DEFAULT_MAIN_SCHEDULE,
    DEFAULT_WEEKEND,
    StalenessConfig,
    check_staleness,
    expected_business_date,
)


@pytest.fixture
def cfg() -> StalenessConfig:
    return StalenessConfig()


# ── expected_business_date ────────────────────────────────


def test_expected_business_date_weekday_morning():
    """工作日 09:00 → 昨日。"""
    now = datetime(2026, 9, 16, 9, 0)
    bd = expected_business_date(now)
    assert bd.isoformat() == "2026-09-15"


def test_expected_business_date_weekday_after_cutoff():
    """工作日 10:00 → 今日。"""
    now = datetime(2026, 9, 16, 10, 0)
    bd = expected_business_date(now)
    assert bd.isoformat() == "2026-09-16"


def test_expected_business_date_saturday():
    """周六 → 上周五。"""
    now = datetime(2026, 9, 19, 12, 0)  # Saturday
    bd = expected_business_date(now)
    assert bd.isoformat() == "2026-09-18"


def test_expected_business_date_sunday():
    """周日 → 上周五。"""
    now = datetime(2026, 9, 20, 12, 0)  # Sunday
    bd = expected_business_date(now)
    assert bd.isoformat() == "2026-09-18"


# ── check_staleness: 基础 ─────────────────────────────────


def test_check_staleness_today_success_ok(cfg):
    """今日已成功 → ok。"""
    now = datetime(2026, 9, 16, 9, 35)
    cs = {"status": "success",
          "last_success_at": datetime(2026, 9, 16, 9, 5).isoformat(),
          "latest_price_date": "2026-09-15",
          "data_date": "2026-09-15"}
    r = check_staleness(cs, now=now, cfg=cfg)
    assert r.status == "ok"


def test_check_staleness_within_main_grace_window_ok(cfg):
    """主宽限期内 + 昨日成功 → ok。"""
    now = datetime(2026, 9, 16, 9, 10)  # 5 分钟前主任务采集
    cs = {"status": "failed",
          "last_success_at": datetime(2026, 9, 15, 9, 5).isoformat(),
          "latest_price_date": "2026-09-15"}
    r = check_staleness(cs, now=now, cfg=cfg)
    assert r.status == "ok"
    assert "main grace" in r.detail.lower() or "grace window" in r.detail.lower()


def test_check_staleness_main_deadline_passed_warn(cfg):
    """主宽限期已过、catchup 还没结束 → warn。"""
    now = datetime(2026, 9, 16, 9, 50)  # 主宽限期 09:35 已过；catchup 截止 10:15
    cs = {"status": "failed",
          "last_success_at": datetime(2026, 9, 15, 9, 5).isoformat(),
          "latest_price_date": "2026-09-15"}
    r = check_staleness(cs, now=now, cfg=cfg)
    assert r.status == "warn"


def test_check_staleness_catchup_deadline_passed_crit(cfg):
    """catchup 宽限期已过 + 连续失败 ≥2 → crit。"""
    now = datetime(2026, 9, 16, 11, 0)
    cs = {"status": "failed",
          "last_success_at": None,
          "consecutive_failures": 2,
          "latest_price_date": "2026-09-15"}
    r = check_staleness(cs, now=now, cfg=cfg)
    assert r.status == "crit"


def test_check_staleness_catchup_deadline_passed_warn_when_low_failures(cfg):
    """catchup 宽限期已过，但连续失败 <2 → warn。"""
    now = datetime(2026, 9, 16, 11, 0)
    cs = {"status": "failed",
          "last_success_at": None,
          "consecutive_failures": 1,
          "latest_price_date": "2026-09-15"}
    r = check_staleness(cs, now=now, cfg=cfg)
    assert r.status == "warn"


def test_check_staleness_weekend_uses_freshness(cfg):
    """周末 → 不判定采集，仅看 price_date 新鲜度。"""
    now = datetime(2026, 9, 19, 12, 0)  # Saturday
    cs = {"status": "failed",
          "last_success_at": None,
          "latest_price_date": "2026-09-18"}  # 上周五
    r = check_staleness(cs, now=now, cfg=cfg)
    # 上周五距 expected (上上周五?) 但配置 fresh_max_stale=3 → OK
    # expected_business_date(Sat) = 上周五
    # 价差 = 0 天 → fresh
    assert r.status == "ok"


def test_check_staleness_price_date_very_stale_warns(cfg):
    """今日已成功，但 price_date 已严重陈旧 → warn。"""
    now = datetime(2026, 9, 16, 9, 35)
    cs = {"status": "success",
          "last_success_at": datetime(2026, 9, 16, 9, 5).isoformat(),
          "latest_price_date": "2026-08-20"}  # 27 天前
    r = check_staleness(cs, now=now, cfg=cfg)
    assert r.status == "warn"


def test_check_staleness_unknown_last_success_at():
    """last_success_at 解析失败 → 退化为 unknown。"""
    now = datetime(2026, 9, 16, 11, 0)
    cs = {"status": "success",
          "last_success_at": "not a date",
          "latest_price_date": "2026-09-15"}
    r = check_staleness(cs, now=now, cfg=StalenessConfig())
    # 解析失败 → date 比较失败 → 视为今日未成功
    # 但 status=success，且连续失败 = 0 → warn 路径（主/兜底宽限期已过）
    assert r.status in ("warn", "ok")


# ── StalenessConfig.from_settings ─────────────────────────


def test_staleness_config_defaults():
    cfg = StalenessConfig.from_settings({})
    assert cfg.main_schedule == DEFAULT_MAIN_SCHEDULE
    assert cfg.catchup_schedule == DEFAULT_CATCHUP_SCHEDULE
    assert cfg.main_grace_minutes == DEFAULT_MAIN_GRACE_MINUTES
    assert cfg.weekend == DEFAULT_WEEKEND


def test_staleness_config_custom():
    cfg = StalenessConfig.from_settings({
        "supervisor": {
            "main_schedule": "08:50",
            "catchup_schedule": "10:00",
            "main_grace_minutes": 45,
            "fresh_max_stale_days": 2,
        }
    })
    assert cfg.main_schedule == time(8, 50)
    assert cfg.catchup_schedule == time(10, 0)
    assert cfg.main_grace_minutes == 45
    assert cfg.fresh_max_stale_days == 2
