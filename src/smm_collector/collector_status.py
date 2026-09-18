"""collector_status.json — 持久化采集器运行状态（Phase E2）。

记录最近一次采集的运行结果，便于：
- ``smm_health.py`` 快速判断系统状态，无需连接数据库
- ops_monitor 聚合运行指标
- 管理员运维中心看 stale_staleness / consecutive_failures

与 auth_status.json 互补：auth 描述登录态；collector 描述采集结果。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("smm_collector.collector_status")

DEFAULT_PATH = Path("/var/lib/smm-collector/collector_status.json")


# ── Collector 运行状态枚举 ────────────────────────────────────────
# 注：与 auth_status.AuthStatus 区分；此处专指「采集运行」状态。

RUN_STATUSES = (
    "success", "failed", "partial_success",
    "skipped", "running", "never_run",
)


@dataclass
class CollectorRunRecord:
    """最近一次采集的完整快照。"""

    status: str = "never_run"
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    last_auth_check_at: str | None = None
    auth_status: str = "AUTH_UNKNOWN"
    latest_price_date: str | None = None
    last_collected_at: str | None = None
    target_date: str | None = None
    data_date: str | None = None
    parsed_rows: int = 0
    validated_rows: int = 0
    inserted_rows: int = 0
    updated_rows: int = 0
    duplicate_rows: int = 0
    consecutive_failures: int = 0
    last_error_type: str | None = None
    last_error_message: str | None = None
    run_id: str | None = None
    next_scheduled_at: str | None = None
    staleness_status: str = "unknown"  # ok / warn / crit / unknown
    staleness_detail: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _default_payload() -> dict[str, Any]:
    return asdict(CollectorRunRecord())


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """原子写 dict 到 JSON（同 auth_status.atomic_write）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".collector_status.", suffix=".tmp",
                                    dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True, default=str)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read(path: Path | None = None) -> dict[str, Any]:
    """读取 collector_status.json；不存在/损坏时返回默认值。"""
    p = path or DEFAULT_PATH
    if not p.exists():
        return _default_payload()
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _default_payload()
        default = _default_payload()
        for k, v in default.items():
            data.setdefault(k, v)
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("collector_status.read failed: %s — return default", e)
        return _default_payload()


def record_run(
    path: Path | None = None,
    *,
    status: str,
    target_date: str | None = None,
    data_date: str | None = None,
    parsed_rows: int | None = None,
    validated_rows: int | None = None,
    inserted_rows: int | None = None,
    updated_rows: int | None = None,
    duplicate_rows: int | None = None,
    auth_status: str | None = None,
    auth_check_at: datetime | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    run_id: str | None = None,
    next_scheduled_at: datetime | None = None,
    latest_price_date: str | None = None,
    last_collected_at: datetime | None = None,
    staleness_status: str | None = None,
    staleness_detail: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """记录一次采集运行的最终状态。

    规则：
      - 成功时清空 last_error_type / last_error_message
      - 失败时 consecutive_failures +1；成功时归零
    """
    now = now or datetime.now()
    p = path or DEFAULT_PATH
    payload = read(p)

    payload["status"] = status if status in RUN_STATUSES else "failed"
    payload["last_attempt_at"] = now.isoformat(timespec="seconds")
    if status in ("success", "partial_success"):
        payload["last_success_at"] = now.isoformat(timespec="seconds")
        payload["consecutive_failures"] = 0
        payload["last_error_type"] = None
        payload["last_error_message"] = None
    else:
        try:
            payload["consecutive_failures"] = int(payload.get("consecutive_failures", 0)) + 1
        except (TypeError, ValueError):
            payload["consecutive_failures"] = 1
        if error_type is not None:
            payload["last_error_type"] = str(error_type)[:100]
        if error_message is not None:
            payload["last_error_message"] = str(error_message)[:500]

    if target_date is not None:
        payload["target_date"] = str(target_date)
    if data_date is not None:
        payload["data_date"] = str(data_date)
    if parsed_rows is not None:
        payload["parsed_rows"] = int(parsed_rows)
    if validated_rows is not None:
        payload["validated_rows"] = int(validated_rows)
    if inserted_rows is not None:
        payload["inserted_rows"] = int(inserted_rows)
    if updated_rows is not None:
        payload["updated_rows"] = int(updated_rows)
    if duplicate_rows is not None:
        payload["duplicate_rows"] = int(duplicate_rows)
    if auth_status is not None:
        payload["auth_status"] = str(auth_status)
    if auth_check_at is not None:
        payload["last_auth_check_at"] = auth_check_at.isoformat(timespec="seconds")
    if run_id is not None:
        payload["run_id"] = str(run_id)
    if next_scheduled_at is not None:
        payload["next_scheduled_at"] = next_scheduled_at.isoformat(timespec="seconds")
    if latest_price_date is not None:
        payload["latest_price_date"] = str(latest_price_date)
    if last_collected_at is not None:
        payload["last_collected_at"] = last_collected_at.isoformat(timespec="seconds")
    if staleness_status is not None:
        payload["staleness_status"] = str(staleness_status)
    if staleness_detail is not None:
        payload["staleness_detail"] = str(staleness_detail)[:500]

    atomic_write(p, payload)
    return payload