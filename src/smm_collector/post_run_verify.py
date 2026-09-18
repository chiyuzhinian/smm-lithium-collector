"""post_run_verify — 采集后数据库验证（Phase E1）。

验证内容（每个都是 bool + reason）：

1. ``verify_row_counts(parsed_rows, validated_rows, inserted, updated)``
   - parsed_rows > 0
   - validated_rows >= parsed_rows * 0.95（允许小比例 invalid）
   - inserted + updated + duplicate ≈ validated_rows

2. ``verify_max_price_date(db_path, expected_data_date)``
   - DB 最新 price_date 应在预期 data_date ± 1 天（页面日期校准允许 ±1）

3. ``verify_recent_collected_at(db_path, since_minutes=30)``
   - DB 最新 collected_at 应在 since_minutes 分钟内

4. ``verify_no_price_wall(rows)`` 二次校验：登录墙均价全空防护

失败时返回 ``PostRunVerifyResult.fail(...)``；调用方根据 error_type 判定。
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .auth_health import check_price_wall_from_rows

logger = logging.getLogger("smm_collector.post_run_verify")


@dataclass
class PostRunVerifyResult:
    ok: bool
    error_type: str | None  # e.g. ZERO_ROWS, VALIDATION_FAILED, PRICE_WALL, DB_ERROR, OK
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def success(cls, checks: dict[str, dict[str, Any]]) -> "PostRunVerifyResult":
        return cls(ok=True, error_type=None, checks=checks)

    @classmethod
    def fail(cls, error_type: str, checks: dict[str, dict[str, Any]],
             reason: str = "") -> "PostRunVerifyResult":
        if error_type not in checks and reason:
            checks[error_type] = {"ok": False, "reason": reason}
        return cls(ok=False, error_type=error_type, checks=checks)


def _check(name: str, ok: bool, **detail: Any) -> dict[str, Any]:
    return {"ok": ok, **{k: v for k, v in detail.items()}}


def verify_row_counts(
    parsed_rows: int,
    validated_rows: int,
    inserted: int,
    updated: int,
    duplicate: int,
) -> PostRunVerifyResult:
    """检查行数合理性。

    任何一项不满足即 fail；error_type 用于上层映射到 AuthStatus/CollectorStatus。
    """
    checks: dict[str, dict[str, Any]] = {}
    checks["row_count_parsed"] = _check("row_count_parsed", parsed_rows > 0,
                                       parsed=parsed_rows)
    if parsed_rows <= 0:
        return PostRunVerifyResult.fail("ZERO_ROWS", checks,
                                        f"parsed_rows={parsed_rows}")
    checks["row_count_validated"] = _check(
        "row_count_validated",
        validated_rows >= parsed_rows * 0.95,
        validated=validated_rows, parsed=parsed_rows,
        ratio=(validated_rows / parsed_rows) if parsed_rows else 0,
    )
    if validated_rows < parsed_rows * 0.95:
        return PostRunVerifyResult.fail("VALIDATION_FAILED", checks,
                                        f"validated={validated_rows} < 95% of parsed={parsed_rows}")

    accounted = inserted + updated + duplicate
    checks["row_count_accounted"] = _check(
        "row_count_accounted",
        abs(accounted - validated_rows) <= max(2, int(validated_rows * 0.05)),
        inserted=inserted, updated=updated, duplicate=duplicate, validated=validated_rows,
    )
    if abs(accounted - validated_rows) > max(2, int(validated_rows * 0.05)):
        return PostRunVerifyResult.fail("VALIDATION_FAILED", checks,
                                        f"inserted+updated+duplicate={accounted} ≠ validated={validated_rows}")

    return PostRunVerifyResult.success(checks)


def verify_max_price_date(
    db_path: Path,
    *,
    expected_data_date: date | None = None,
    tolerance_days: int = 1,
) -> PostRunVerifyResult:
    """DB 最新 price_date 应在 expected ± tolerance_days 内。

    如果 expected_data_date 为 None，只检查「DB 至少有数据」+「max 不为空」。
    """
    checks: dict[str, dict[str, Any]] = {}
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as e:
        return PostRunVerifyResult.fail(
            "DB_ERROR", {"db_connect": {"ok": False, "reason": str(e)}},
            f"connect failed: {e}",
        )
    try:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT MAX(price_date), COUNT(*) FROM lithium_spot_prices "
            "WHERE price_date IS NOT NULL AND price_date != ''"
        ).fetchone()
        max_pd, total = row[0], row[1] or 0
        checks["db_total"] = _check("db_total", total > 0, total=total)
        if total <= 0:
            return PostRunVerifyResult.fail("ZERO_ROWS", checks,
                                            f"db has 0 valid rows")
        checks["db_max_price_date"] = _check(
            "db_max_price_date", max_pd is not None and max_pd != "",
            max_price_date=max_pd,
        )
        if expected_data_date is not None and max_pd:
                # SQLite date() 解析 'YYYY-MM-DD'
                try:
                    max_pd_date = date.fromisoformat(max_pd[:10])
                except (TypeError, ValueError):
                    return PostRunVerifyResult.fail(
                        "DB_ERROR", checks,
                        f"max price_date not parseable: {max_pd}",
                    )
                delta = (expected_data_date - max_pd_date).days
                checks["db_price_date_freshness"] = _check(
                    "db_price_date_freshness",
                    abs(delta) <= tolerance_days,
                    max=max_pd_date.isoformat(), expected=expected_data_date.isoformat(),
                    delta_days=delta, tolerance=tolerance_days,
                )
                if abs(delta) > tolerance_days:
                    return PostRunVerifyResult.fail(
                        "POST_RUN_VERIFY_FAILED", checks,
                        f"max price_date {max_pd_date} differs from expected {expected_data_date} by {delta}d",
                    )
        return PostRunVerifyResult.success(checks)
    except sqlite3.Error as e:
        return PostRunVerifyResult.fail("DB_ERROR", checks, f"query failed: {e}")
    finally:
        conn.close()


def verify_recent_collected_at(
    db_path: Path,
    *,
    since_minutes: int = 30,
    now: datetime | None = None,
) -> PostRunVerifyResult:
    """DB 最新 collected_at 应在 since_minutes 分钟内（防止「看似成功但实为旧数据」）。"""
    checks: dict[str, dict[str, Any]] = {}
    now = now or datetime.now()
    threshold = now - timedelta(minutes=since_minutes)
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as e:
        return PostRunVerifyResult.fail("DB_ERROR", {"db_connect": {"ok": False, "reason": str(e)}})
    try:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT MAX(collected_at), COUNT(*) FROM lithium_spot_prices"
        ).fetchone()
        max_ca, total = row[0], row[1] or 0
        checks["db_total"] = _check("db_total", total > 0, total=total)
        if total <= 0:
            return PostRunVerifyResult.fail("ZERO_ROWS", checks, "db empty")
        # SQLite datetime 解析
        try:
            max_dt = datetime.fromisoformat(max_ca[:19])
        except (TypeError, ValueError):
            return PostRunVerifyResult.fail("POST_RUN_VERIFY_FAILED", checks,
                                           f"collected_at not parseable: {max_ca}")
        delta_min = (now - max_dt).total_seconds() / 60
        checks["db_collected_at_freshness"] = _check(
            "db_collected_at_freshness",
            max_dt >= threshold,
            max_collected_at=max_dt.isoformat(timespec="seconds"),
            threshold=threshold.isoformat(timespec="seconds"),
            minutes_ago=delta_min,
        )
        if max_dt < threshold:
            return PostRunVerifyResult.fail(
                "POST_RUN_VERIFY_FAILED", checks,
                f"max collected_at {max_dt} is {delta_min:.1f}min ago (> {since_minutes}min)",
            )
        return PostRunVerifyResult.success(checks)
    except sqlite3.Error as e:
        return PostRunVerifyResult.fail("DB_ERROR", checks, f"query failed: {e}")
    finally:
        conn.close()


def verify_no_price_wall(rows: list[dict], *, ratio_threshold: float = 0.8,
                         min_rows: int = 10) -> PostRunVerifyResult:
    """二次校验：登录墙均价全空防护（与 auth_health.check_price_wall_from_rows 配合）。

    失败 error_type = PRICE_WALL（属于 AUTH_EXPIRED 衍生）。
    """
    suspected, ratio, reason = check_price_wall_from_rows(
        rows, threshold=ratio_threshold, min_rows=min_rows)
    if suspected:
        return PostRunVerifyResult.fail(
            "PRICE_WALL",
            {"price_wall": {"ok": False, "reason": reason, "ratio": ratio}},
            reason,
        )
    return PostRunVerifyResult.success(
        {"price_wall": {"ok": True, "reason": reason, "ratio": ratio}}
    )