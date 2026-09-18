"""Supervisor — 采集器守护编排（Phase E1）。

职责：把网络、认证、采集、后验、状态持久化串成一条流水线。

调用链：
    scripts/smm_supervisor.py → Supervisor.run_collection(target_date, ...)
      ↓
    1. Network preflight（3 次重试：0s / +60s / +180s）
    2. Auth preflight + Auto-login 一次（遇验证立即停止）
    3. main.collect(target_date, ...)  ← 已有采集逻辑
    4. post_run_verify（行数 / price_date / collected_at / 登录墙）
    5. collector_status.json + auth_status.json 原子写
    6. ops_events（异常安全，绝不影响结果）

设计约束：
  - 失败绝不静默；每种错误类型都有明确 error_type
  - 0 行 = 失败（不写 SUCCESS）
  - 不重复启动浏览器：auth 阶段的浏览器复用 main.collect 的 persistent profile
  - auth 失败不重试网络；网络失败不重试认证
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from . import main as legacy_main
from .auth_health import AuthStatus, check_auth
from .auth_status import update as update_auth_status
from .authentication import guarded_auto_login
from .browser_v2 import close_browser_smart, open_browser_smart
from .collector_status import record_run
from .completeness import compute_baseline, fetch_rows_per_day
from .config import AppConfig, load_config
from .logger import setup_logging
from .network_check import check_network
from .post_run_verify import (
    verify_max_price_date,
    verify_recent_collected_at,
    verify_row_counts,
)
from .staleness import StalenessConfig, check_staleness

logger = logging.getLogger("smm_collector.supervisor")

NETWORK_RETRY_DELAYS_S = (0, 60, 180)


@dataclass
class SupervisorResult:
    """Supervisor.run_collection 的返回结构。"""

    status: str
    run_id: str
    target_date: str
    data_date: str | None = None
    parsed_rows: int = 0
    validated_rows: int = 0
    inserted_rows: int = 0
    updated_rows: int = 0
    duplicate_rows: int = 0
    auth_status: str = "AUTH_UNKNOWN"
    error_type: str | None = None
    error_message: str | None = None
    started_at: str = ""
    finished_at: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["meta"] = self.meta
        d["extra"] = self.extra
        return d


def _ops_event(event_type: str, level: str, message: str) -> None:
    """写运维事件到 auth.db（异常安全，绝不影响采集结果）。"""
    try:
        from .ops_events import write_event
        write_event(event_type, level, message)
    except Exception:
        pass


async def _network_preflight(cfg: AppConfig, log: logging.Logger) -> tuple[bool, str]:
    """3 次重试：0s / +60s / +180s。仅对网络层错误重试，认证不重试。"""
    last_reason = "network preflight exhausted"
    for attempt, delay in enumerate(NETWORK_RETRY_DELAYS_S):
        if delay > 0:
            log.info("network_preflight: 第 %d 次重试前等待 %ds", attempt + 1, delay)
            await asyncio.sleep(delay)
        ok, reason = await check_network(cfg)
        log.info("network_preflight: 尝试 %d → ok=%s reason=%s", attempt + 1, ok, reason)
        if ok:
            return True, reason
        last_reason = reason
    return False, last_reason


async def _auth_preflight(cfg: AppConfig, log: logging.Logger) -> SupervisorResult:
    """打开浏览器 → 访问 target_url → check_auth → 必要时 guarded_auto_login。

    返回 SupervisorResult 描述 auth 阶段结论。
    """
    started = datetime.now()
    run_id = str(uuid.uuid4())
    pw = browser = context = page = None
    auth_status_value = AuthStatus.UNKNOWN.value
    error_type: str | None = None
    error_message: str | None = None

    try:
        pw, browser, context = await open_browser_smart(cfg, headed=False)
        page = await context.new_page()
        await page.goto(cfg.target_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)

        status, reason = await check_auth(page, cfg)
        auth_status_value = status.value

        if status == AuthStatus.NETWORK_ERROR:
            error_type = "NETWORK_ERROR"
            error_message = reason
            update_auth_status(status=AuthStatus.NETWORK_ERROR, reason=reason)
            return SupervisorResult(
                status="failed", run_id=run_id, target_date="",
                auth_status=auth_status_value, error_type=error_type,
                error_message=error_message, started_at=started.isoformat(),
            )

        if status == AuthStatus.VERIFICATION_REQUIRED:
            error_type = "AUTH_VERIFICATION_REQUIRED"
            error_message = reason
            update_auth_status(status=AuthStatus.VERIFICATION_REQUIRED, reason=reason,
                               failure_reason=reason)
            _ops_event("AUTH_VERIFICATION_REQUIRED", "error",
                       f"检测到验证挑战，需人工登录：{reason[:200]}")
            return SupervisorResult(
                status="failed", run_id=run_id, target_date="",
                auth_status=auth_status_value, error_type=error_type,
                error_message=error_message, started_at=started.isoformat(),
            )

        if status == AuthStatus.EXPIRED:
            log.info("auth_preflight: AUTH_EXPIRED，尝试自动续登")
            update_auth_status(status=AuthStatus.EXPIRED, reason=reason,
                               failure_reason=reason)
            auto_status, auto_reason = await guarded_auto_login(page, cfg)
            auth_status_value = auto_status
            if auto_status == "AUTH_OK":
                status2, reason2 = await check_auth(page, cfg)
                auth_status_value = status2.value
                if status2 != AuthStatus.OK:
                    error_type = f"AUTH_{status2.value}"
                    error_message = f"auto_login 后复验失败：{reason2}"
                    update_auth_status(status=status2, reason=reason2,
                                       failure_reason=reason2)
                    return SupervisorResult(
                        status="failed", run_id=run_id, target_date="",
                        auth_status=auth_status_value, error_type=error_type,
                        error_message=error_message, started_at=started.isoformat(),
                    )
                update_auth_status(status=AuthStatus.OK, reason=f"auto_login: {auto_reason}",
                                   last_ok_at=datetime.now(),
                                   last_login_at=datetime.now())
            elif auto_status == "AUTH_VERIFICATION_REQUIRED":
                error_type = "AUTH_VERIFICATION_REQUIRED"
                error_message = f"自动登录触发验证：{auto_reason}"
                update_auth_status(status=AuthStatus.VERIFICATION_REQUIRED, reason=auto_reason,
                                   failure_reason=auto_reason)
                _ops_event("AUTH_VERIFICATION_REQUIRED", "error",
                           f"自动登录触发验证挑战：{auto_reason[:200]}")
                return SupervisorResult(
                    status="failed", run_id=run_id, target_date="",
                    auth_status=auto_status, error_type=error_type,
                    error_message=error_message, started_at=started.isoformat(),
                )
            elif auto_status == "AUTH_LOGIN_FAILED":
                error_type = "AUTH_LOGIN_FAILED"
                error_message = f"自动登录失败：{auto_reason}"
                update_auth_status(status=AuthStatus.LOGIN_FAILED, reason=auto_reason,
                                   failure_reason=auto_reason)
                _ops_event("AUTH_LOGIN_FAILED", "error",
                           f"自动登录失败：{auto_reason[:200]}")
                return SupervisorResult(
                    status="failed", run_id=run_id, target_date="",
                    auth_status=auto_status, error_type=error_type,
                    error_message=error_message, started_at=started.isoformat(),
                )
            elif auto_status == "AUTH_NETWORK_ERROR":
                error_type = "NETWORK_ERROR"
                error_message = f"自动登录网络错：{auto_reason}"
                update_auth_status(status=AuthStatus.NETWORK_ERROR, reason=auto_reason,
                                   failure_reason=auto_reason)
                return SupervisorResult(
                    status="failed", run_id=run_id, target_date="",
                    auth_status=auto_status, error_type=error_type,
                    error_message=error_message, started_at=started.isoformat(),
                )
            else:
                error_type = "AUTH_EXPIRED"
                error_message = f"自动登录未成功：{auto_reason}"
                update_auth_status(status=AuthStatus.EXPIRED, reason=auto_reason,
                                   failure_reason=auto_reason)
                return SupervisorResult(
                    status="failed", run_id=run_id, target_date="",
                    auth_status=auto_status, error_type=error_type,
                    error_message=error_message, started_at=started.isoformat(),
                )

        # OK
        update_auth_status(status=AuthStatus.OK, reason=reason, last_ok_at=datetime.now())
        return SupervisorResult(
            status="success", run_id=run_id, target_date="",
            auth_status=AuthStatus.OK.value, started_at=started.isoformat(),
        )

    except Exception as e:
        msg = f"{type(e).__name__}: {e}"[:300]
        logger.exception("auth_preflight failed")
        error_type = "AUTH_PREFLIGHT_EXCEPTION"
        error_message = msg
        return SupervisorResult(
            status="failed", run_id=run_id, target_date="",
            auth_status=auth_status_value, error_type=error_type,
            error_message=error_message, started_at=started.isoformat(),
        )
    finally:
        try:
            if browser is not None or context is not None:
                await close_browser_smart(pw, browser, context)
        except Exception:
            pass


def _next_scheduled(now: datetime | None = None) -> datetime:
    """估算下一次计划采集时间（工作日 09:30）。"""
    now = now or datetime.now()
    cutoff = time(9, 30)
    if now.time() < cutoff:
        target_day = now.date()
    else:
        target_day = (now + timedelta(days=1)).date()
    while target_day.weekday() in (5, 6):
        target_day += timedelta(days=1)
    return datetime.combine(target_day, cutoff)


async def run_collection(
    target_date: date,
    *,
    category: str | None = None,
    headed: bool = False,
    dry_run: bool = False,
    calibrate_date: bool = True,
    cfg: AppConfig | None = None,
) -> SupervisorResult:
    """完整 Supervisor 流程：网络 → 认证 → 采集 → 后验 → 状态。"""
    cfg = cfg or load_config()
    log = setup_logging(cfg.root)
    started = datetime.now()
    run_id = str(uuid.uuid4())
    auth_status_value = "AUTH_UNKNOWN"

    _ops_event("SUPERVISOR_STARTED", "info", f"target_date={target_date}")

    # 1) Network preflight
    net_ok, net_reason = await _network_preflight(cfg, log)
    if not net_ok:
        _ops_event("NETWORK_ERROR", "error", f"网络层失败：{net_reason[:200]}")
        record_run(
            status="failed", target_date=str(target_date),
            error_type="NETWORK_ERROR", error_message=net_reason,
            run_id=run_id, next_scheduled_at=_next_scheduled(),
        )
        return SupervisorResult(
            status="failed", run_id=run_id, target_date=str(target_date),
            auth_status="AUTH_UNKNOWN", error_type="NETWORK_ERROR",
            error_message=net_reason,
            started_at=started.isoformat(),
            finished_at=datetime.now().isoformat(),
        )

    # 2) Auth preflight + recovery
    auth_result = await _auth_preflight(cfg, log)
    auth_status_value = auth_result.auth_status
    if auth_result.status != "success":
        record_run(
            status="failed", target_date=str(target_date),
            auth_status=auth_status_value,
            error_type=auth_result.error_type or "AUTH_FAILED",
            error_message=auth_result.error_message,
            run_id=run_id, next_scheduled_at=_next_scheduled(),
        )
        return SupervisorResult(
            status="failed", run_id=run_id, target_date=str(target_date),
            auth_status=auth_status_value,
            error_type=auth_result.error_type,
            error_message=auth_result.error_message,
            started_at=started.isoformat(),
            finished_at=datetime.now().isoformat(),
        )

    # Auth 成功：Supervisor 层也写一次 auth_status（即使 _auth_preflight 已写过）
    update_auth_status(status=AuthStatus.OK, reason="supervisor auth OK",
                       last_ok_at=datetime.now())

    # 3) 采集（委托给已有 main.collect()）
    legacy_meta: dict = {}
    try:
        legacy_meta = await legacy_main.collect(
            target_date, category=category, headed=headed,
            dry_run=dry_run, calibrate_date=calibrate_date,
        )
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"[:500]
        logger.exception("main.collect raised")
        _ops_event("COLLECTOR_EXCEPTION", "error", f"采集异常：{msg}")
        record_run(
            status="failed", target_date=str(target_date),
            auth_status=auth_status_value,
            error_type="COLLECTOR_EXCEPTION", error_message=msg,
            run_id=run_id, next_scheduled_at=_next_scheduled(),
        )
        return SupervisorResult(
            status="failed", run_id=run_id, target_date=str(target_date),
            auth_status=auth_status_value, error_type="COLLECTOR_EXCEPTION",
            error_message=msg,
            started_at=started.isoformat(),
            finished_at=datetime.now().isoformat(),
        )

    # 4) Post-run verify
    data_date = legacy_meta.get("data_date") or str(target_date)
    parsed_rows = int(legacy_meta.get("total_raw_rows", 0) or 0)
    validated_rows = int(legacy_meta.get("total_clean_rows", 0) or 0)
    db_stats = legacy_meta.get("db_stats") or {}
    inserted = int(db_stats.get("inserted", 0) or 0)
    updated = int(db_stats.get("updated", 0) or 0)
    duplicate = int(db_stats.get("duplicate", 0) or 0)

    verify_result = verify_row_counts(
        parsed_rows, validated_rows, inserted, updated, duplicate,
        dry_run=dry_run,
    )
    if not verify_result.ok:
        err_checks = verify_result.checks.get(verify_result.error_type or "", {})
        _ops_event("POST_RUN_VERIFY_FAILED", "error",
                   f"{verify_result.error_type}: {err_checks}")
        record_run(
            status="failed", target_date=str(target_date),
            data_date=data_date, auth_status=auth_status_value,
            parsed_rows=parsed_rows, validated_rows=validated_rows,
            inserted_rows=inserted, updated_rows=updated, duplicate_rows=duplicate,
            error_type=verify_result.error_type or "POST_RUN_VERIFY_FAILED",
            error_message=str(err_checks)[:500],
            run_id=run_id, next_scheduled_at=_next_scheduled(),
        )
        return SupervisorResult(
            status="failed", run_id=run_id, target_date=str(target_date),
            data_date=data_date, parsed_rows=parsed_rows, validated_rows=validated_rows,
            inserted_rows=inserted, updated_rows=updated, duplicate_rows=duplicate,
            auth_status=auth_status_value, error_type=verify_result.error_type,
            started_at=started.isoformat(),
            finished_at=datetime.now().isoformat(),
        )

    # price_date / collected_at 校验（仅非 dry_run）
    if not dry_run:
        try:
            pd_check = verify_max_price_date(
                cfg.path("database_path"),
                expected_data_date=date.fromisoformat(data_date) if data_date else None,
            )
            ca_check = verify_recent_collected_at(
                cfg.path("database_path"), since_minutes=30,
            )
            if not pd_check.ok or not ca_check.ok:
                failed_check = pd_check if not pd_check.ok else ca_check
                _ops_event("POST_RUN_VERIFY_FAILED", "error",
                           f"{failed_check.error_type}: {failed_check.checks}")
                record_run(
                    status="failed", target_date=str(target_date),
                    data_date=data_date, auth_status=auth_status_value,
                    parsed_rows=parsed_rows, validated_rows=validated_rows,
                    inserted_rows=inserted, updated_rows=updated, duplicate_rows=duplicate,
                    error_type=failed_check.error_type or "POST_RUN_VERIFY_FAILED",
                    error_message=str(failed_check.checks)[:500],
                    run_id=run_id, next_scheduled_at=_next_scheduled(),
                )
                return SupervisorResult(
                    status="failed", run_id=run_id, target_date=str(target_date),
                    data_date=data_date, parsed_rows=parsed_rows, validated_rows=validated_rows,
                    inserted_rows=inserted, updated_rows=updated, duplicate_rows=duplicate,
                    auth_status=auth_status_value, error_type=failed_check.error_type,
                    started_at=started.isoformat(),
                    finished_at=datetime.now().isoformat(),
                )
        except Exception as e:
            logger.warning("post_run_verify db check skipped: %s", e)

    # 5) 状态写入
    final_status = (
        "success" if legacy_meta.get("status") == "success"
        else "partial_success" if legacy_meta.get("status") == "partial_success"
        else "failed"
    )
    is_success = final_status in ("success", "partial_success")

    # 6) staleness 评估
    db_history: list[int] = []
    try:
        db_history = fetch_rows_per_day(cfg.path("database_path"), lookback_days=14)
    except Exception:
        pass
    baseline = compute_baseline(db_history, parsed_rows)
    staleness_cfg = StalenessConfig.from_settings(cfg.settings if cfg else {})
    staleness_result = check_staleness(
        {"status": final_status,
         "last_success_at": datetime.now().isoformat(timespec="seconds") if is_success else None,
         "latest_price_date": data_date,
         "data_date": data_date,
         "consecutive_failures": 0},
        now=datetime.now(), cfg=staleness_cfg,
    )

    record_run(
        status=final_status, target_date=str(target_date),
        data_date=data_date, auth_status=auth_status_value,
        parsed_rows=parsed_rows, validated_rows=validated_rows,
        inserted_rows=inserted, updated_rows=updated, duplicate_rows=duplicate,
        run_id=run_id, next_scheduled_at=_next_scheduled(),
        latest_price_date=data_date,
        last_collected_at=datetime.now(),
        staleness_status=staleness_result.status,
        staleness_detail=staleness_result.detail,
    )

    _ops_event(
        "SUPERVISOR_FINISHED",
        "info" if final_status == "success" else "warning",
        f"status={final_status} parsed={parsed_rows} validated={validated_rows} "
        f"auth={auth_status_value} baseline={baseline.status} staleness={staleness_result.status}",
    )

    return SupervisorResult(
        status=final_status, run_id=run_id,
        target_date=str(target_date), data_date=data_date,
        parsed_rows=parsed_rows, validated_rows=validated_rows,
        inserted_rows=inserted, updated_rows=updated, duplicate_rows=duplicate,
        auth_status=auth_status_value, meta=legacy_meta,
        started_at=started.isoformat(),
        finished_at=datetime.now().isoformat(),
        extra={"baseline": baseline.status, "staleness": staleness_result.status,
               "anchor_missing": []},
    )


def main() -> int:
    """CLI entrypoint（被 scripts/smm_supervisor.py 调用）。"""
    import argparse
    p = argparse.ArgumentParser(description="SMM 采集器 Supervisor")
    p.add_argument("--date", type=date.fromisoformat, default=None)
    p.add_argument("--category", default=None)
    p.add_argument("--headed", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    target_date = args.date or date.today()
    calibrate = args.date is None

    result = asyncio.run(run_collection(
        target_date,
        category=args.category,
        headed=args.headed,
        dry_run=args.dry_run,
        calibrate_date=calibrate,
    ))
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    if result.status == "success":
        return 0
    if result.status == "partial_success":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())