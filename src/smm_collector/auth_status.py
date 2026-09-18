"""auth_status.json — 持久化 Auth 健康状态（Phase E2）。

设计要点：
- 原子写：先写 ``.tmp``，再 ``os.replace``，防止半截文件被读取。
- 不存敏感信息（密码/cookie/token/username 完整值）。
- Supervisor / ``smm_health.py`` 都读这份文件，无需反复拉起浏览器。
- 与 ``collector_status.json`` 互补：auth 描述登录态；collector 描述采集结果。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .auth_health import AuthStatus

logger = logging.getLogger("smm_collector.auth_status")

DEFAULT_PATH = Path("/var/lib/smm-collector/auth_status.json")

# 状态字段白名单 — 防止意外写入敏感数据。
ALLOWED_KEYS = {
    "last_check_at", "last_ok_at", "last_login_at",
    "status", "reason", "failure_reason",
    "consecutive_failures", "auto_login_attempts",
    "profile_dir", "persistent_profile",
}


def _default_payload() -> dict[str, Any]:
    return {
        "last_check_at": None,
        "last_ok_at": None,
        "last_login_at": None,
        "status": AuthStatus.UNKNOWN.value,
        "reason": "never checked",
        "failure_reason": None,
        "consecutive_failures": 0,
        "auto_login_attempts": 0,
        "profile_dir": None,
        "persistent_profile": False,
    }


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """原子写 dict 到 JSON：先写 .tmp，fsync，再 os.replace。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".auth_status.", suffix=".tmp",
                                    dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
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
    """读取 auth_status.json；不存在或损坏时返回默认值（从不抛出）。"""
    p = path or DEFAULT_PATH
    if not p.exists():
        return _default_payload()
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _default_payload()
        # 补齐缺失字段
        default = _default_payload()
        for k, v in default.items():
            data.setdefault(k, v)
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("auth_status.read failed: %s — return default", e)
        return _default_payload()


def update(
    path: Path | None = None,
    *,
    status: AuthStatus | None = None,
    reason: str | None = None,
    failure_reason: str | None = None,
    consecutive_failures: int | None = None,
    auto_login_attempts: int | None = None,
    last_check_at: datetime | None = None,
    last_ok_at: datetime | None = None,
    last_login_at: datetime | None = None,
    profile_dir: str | None = None,
    persistent_profile: bool | None = None,
) -> dict[str, Any]:
    """原子读取-合并-写回。

    仅更新提供的字段；其他字段保留。每次调用都会写 ``last_check_at``（除非显式为 None）。
    """
    p = path or DEFAULT_PATH
    payload = read(p)

    if status is not None:
        payload["status"] = status.value if isinstance(status, AuthStatus) else str(status)
    if reason is not None:
        payload["reason"] = str(reason)[:300]
    if failure_reason is not None:
        payload["failure_reason"] = str(failure_reason)[:300]
    elif status is not None and isinstance(status, AuthStatus) and status == AuthStatus.OK:
        # 成功时清掉失败原因，避免误导
        payload["failure_reason"] = None

    if consecutive_failures is not None:
        try:
            payload["consecutive_failures"] = max(0, int(consecutive_failures))
        except (TypeError, ValueError):
            pass
    elif status is not None:
        if isinstance(status, AuthStatus):
            if status == AuthStatus.OK:
                payload["consecutive_failures"] = 0
            else:
                payload["consecutive_failures"] = int(payload.get("consecutive_failures", 0)) + 1

    if auto_login_attempts is not None:
        try:
            payload["auto_login_attempts"] = max(0, int(auto_login_attempts))
        except (TypeError, ValueError):
            pass

    if last_check_at is not None:
        payload["last_check_at"] = last_check_at.isoformat(timespec="seconds")
    else:
        payload["last_check_at"] = datetime.now().isoformat(timespec="seconds")

    if last_ok_at is not None:
        payload["last_ok_at"] = last_ok_at.isoformat(timespec="seconds")
    elif status == AuthStatus.OK and last_ok_at is None:
        payload["last_ok_at"] = datetime.now().isoformat(timespec="seconds")

    if last_login_at is not None:
        payload["last_login_at"] = last_login_at.isoformat(timespec="seconds")

    if profile_dir is not None:
        payload["profile_dir"] = str(profile_dir)
    if persistent_profile is not None:
        payload["persistent_profile"] = bool(persistent_profile)

    # 仅写入白名单字段
    clean = {k: payload.get(k) for k in ALLOWED_KEYS}
    atomic_write(p, clean)
    return clean