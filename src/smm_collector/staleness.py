"""staleness — 采集停更检测（Phase E3）。

设计原则：
- **不**用「today - price_date > 1」作为唯一判定（周末/节假日/发布节奏）。
- 以「计划任务是否在预期时间成功完成」为核心。
- ``price_date`` 只作为业务新鲜度辅助信号。

判定逻辑：
  - 工作日（Mon-Fri）才是「计划采集日」；周末由 catchup 处理。
  - 主任务时间 09:05；宽限期 = 09:05 + ``main_grace_minutes``（默认 30 → 09:35）
  - 兜底任务时间 09:30；宽限期 = 09:30 + ``catchup_grace_minutes``（默认 45 → 10:15）
  - 若当前时间 > 主宽限期 且 last_success_at < 今日 09:05 → CRIT
  - 若当前时间 > 兜底宽限期 且 last_success_at < 今日 09:30 → WARN
  - 业务新鲜度（price_date）：
      - last_price_date < expected_business_date → WARN
      - last_price_date 在 expected - 3 ~ expected → OK
      - 更老 → WARN（业务陈旧但不阻断采集器状态本身）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

logger = logging.getLogger("smm_collector.staleness")

DEFAULT_MAIN_GRACE_MINUTES = 30
DEFAULT_CATCHUP_GRACE_MINUTES = 45
DEFAULT_MAIN_SCHEDULE = time(9, 5)
DEFAULT_CATCHUP_SCHEDULE = time(9, 30)
DEFAULT_WEEKEND = (5, 6)  # Saturday=5, Sunday=6 (Mon=0)


@dataclass
class StalenessConfig:
    main_schedule: time = DEFAULT_MAIN_SCHEDULE
    catchup_schedule: time = DEFAULT_CATCHUP_SCHEDULE
    main_grace_minutes: int = DEFAULT_MAIN_GRACE_MINUTES
    catchup_grace_minutes: int = DEFAULT_CATCHUP_GRACE_MINUTES
    weekend: tuple[int, ...] = (5, 6)  # Saturday=5, Sunday=6 (Mon=0)
    # 业务新鲜度阈值
    fresh_max_stale_days: int = 3  # price_date 距 expected 不超过 3 天 → 视为新鲜
    fresh_warn_stale_days: int = 14  # 超过 14 天 → 业务陈旧 WARN

    @classmethod
    def from_settings(cls, settings: dict) -> "StalenessConfig":
        sup = (settings or {}).get("supervisor", {}) or {}
        return cls(
            main_schedule=_parse_time(sup.get("main_schedule", "09:05")),
            catchup_schedule=_parse_time(sup.get("catchup_schedule", "09:30")),
            main_grace_minutes=int(sup.get("main_grace_minutes", DEFAULT_MAIN_GRACE_MINUTES)),
            catchup_grace_minutes=int(sup.get("catchup_grace_minutes", DEFAULT_CATCHUP_GRACE_MINUTES)),
            weekend=tuple(sup.get("weekend", (5, 6))),
            fresh_max_stale_days=int(sup.get("fresh_max_stale_days", 3)),
            fresh_warn_stale_days=int(sup.get("fresh_warn_stale_days", 14)),
        )


def _parse_time(s: str) -> time:
    """解析 'HH:MM' / 'HH:MM:SS' 字符串。"""
    try:
        parts = s.split(":")
        return time(int(parts[0]), int(parts[1]))
    except Exception:
        return time(9, 5)


def expected_business_date(now: datetime, weekend: tuple[int, ...] = DEFAULT_WEEKEND) -> date:
    """SMM 数据应已发布的「业务日」。

    规则：若 now < 09:30 当日，预计发布的是昨日（SMM 周一习惯晚发布周六数据）。
    简化版：now 是工作日且 < 09:30 → 昨日；否则 → 今日。
    """
    today = now.date()
    if today.weekday() in weekend:
        # 周末：期望 last_business_date = 上周五
        # Saturday (5) → 1 天前 ; Sunday (6) → 2 天前
        days_back = today.weekday() - 4
        return today - timedelta(days=days_back)
    if now.time() < DEFAULT_CATCHUP_SCHEDULE:
        return today - timedelta(days=1)
    return today


DEFAULT_WEEKEND = (5, 6)


@dataclass
class StalenessResult:
    status: str  # ok / warn / crit / unknown
    detail: str
    run_status: str | None = None
    consecutive_failures: int = 0
    expected_business_date: str | None = None
    last_success_at: str | None = None
    last_price_date: str | None = None
    next_action: str | None = None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def check_staleness(
    collector_status: dict[str, Any],
    now: datetime,
    cfg: StalenessConfig,
) -> StalenessResult:
    """基于 collector_status.json + 当前时间判定停更状态。"""
    run_status = collector_status.get("status")
    last_success_at = _parse_dt(collector_status.get("last_success_at"))
    last_price_date_str = collector_status.get("latest_price_date") or collector_status.get("data_date")
    last_price_date = _parse_date(last_price_date_str)
    try:
        consecutive_failures = int(collector_status.get("consecutive_failures", 0) or 0)
    except (TypeError, ValueError):
        consecutive_failures = 0

    expected_bd = expected_business_date(now)
    expected_bd_str = expected_bd.isoformat()

    result = StalenessResult(
        status="unknown",
        detail="",
        run_status=run_status,
        consecutive_failures=consecutive_failures,
        expected_business_date=expected_bd_str,
        last_success_at=last_success_at.isoformat(timespec="seconds") if last_success_at else None,
        last_price_date=last_price_date_str,
    )

    # 周末：仅监控业务新鲜度（不判定采集运行计划）
    if now.weekday() in cfg.weekend:
        result.status = "ok"  # 默认 ok；新鲜度检测可降级
        result.detail = f"weekend: only monitoring price freshness (expected={expected_bd_str})"
        result.next_action = "no scheduled run on weekends"
        result = _assess_business_freshness(result, last_price_date, expected_bd, cfg)
        return result

    # 工作日：计划采集时间判定
    main_deadline = datetime.combine(now.date(), cfg.main_schedule) + timedelta(
        minutes=cfg.main_grace_minutes)
    catchup_deadline = datetime.combine(now.date(), cfg.catchup_schedule) + timedelta(
        minutes=cfg.catchup_grace_minutes)

    if run_status == "success" and last_success_at and last_success_at.date() == now.date():
        # 今日已成功 → OK
        result.status = "ok"
        result.detail = f"today already succeeded at {last_success_at.isoformat(timespec='seconds')}"
        result.next_action = "no action needed"
        # 顺便评估业务新鲜度（作为辅助）
        result = _assess_business_freshness(result, last_price_date, expected_bd, cfg)
        return result

    if now < main_deadline:
        # 还在宽限期内，宽容
        if last_success_at and last_success_at.date() >= now.date() - timedelta(days=1):
            result.status = "ok"
            result.detail = "within main grace window, previous day success"
            result.next_action = "wait for main run"
            return result
        result.status = "warn"
        result.detail = "within main grace window but no recent success"
        result.next_action = "wait for main run"
        return result

    if now < catchup_deadline:
        # 主任务宽限期已过，兜底尚未结束
        result.status = "warn"
        result.detail = f"main deadline passed, catchup window ends at {catchup_deadline.isoformat(timespec='seconds')}"
        result.next_action = "wait for catchup run"
        return result

    # 兜底宽限期已过
    if consecutive_failures >= 2:
        result.status = "crit"
        result.detail = (
            f"catchup deadline passed, consecutive_failures={consecutive_failures}, "
            f"expected_business_date={expected_bd_str}"
        )
        result.next_action = "manual intervention / check auth"
        return result

    result.status = "warn"
    result.detail = f"catchup deadline passed (consecutive_failures={consecutive_failures})"
    result.next_action = "investigate auth/network"
    return result


def _assess_business_freshness(
    result: StalenessResult,
    last_price_date: date | None,
    expected_bd: date,
    cfg: StalenessConfig,
) -> StalenessResult:
    """在已有状态基础上叠加 price_date 新鲜度评估。"""
    if last_price_date is None:
        # 业务新鲜度不可知 → 不影响主状态
        return result

    days_stale = (expected_bd - last_price_date).days
    if days_stale < 0:
        # price_date 超过预期（未来）→ 数据异常但采集状态可能 OK
        return result
    if days_stale <= cfg.fresh_max_stale_days:
        # 价格新鲜
        return result
    if days_stale <= cfg.fresh_warn_stale_days:
        # 价格略陈旧 → 状态降级 warn（如果还是 ok）
        if result.status == "ok":
            result.status = "warn"
            result.detail += f"; price_date stale {days_stale}d (max {cfg.fresh_max_stale_days}d)"
        return result
    # 价格显著陈旧
    if result.status in ("ok", "warn"):
        result.status = "warn"
    result.detail += f"; price_date very stale {days_stale}d (>{cfg.fresh_warn_stale_days}d)"
    return result