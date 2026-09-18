#!/usr/bin/env python3
# scripts/smm_health_sentinel.py
#
# SMM 采集健康哨兵（2026-09-18 V2 切换新增）。
#
# 目的：在 09:05 主任务 + 09:30 catchup 之外，**只读**地检查今日是否成功采集。
#   - 不打开浏览器
#   - 不写价格数据库
#   - 不调用 supervisor / collector
#
# 触发时间：工作日 10:00（cron 由 install_cron.sh 安装）
#
# 检查项：
#   1. collector_status.json：今日 last_success_at 是否存在
#      → 不存在：COLLECTOR_STALE
#   2. auth_status.json：当前认证状态
#      → AUTH_EXPIRED / AUTH_VERIFICATION_REQUIRED / AUTH_LOGIN_FAILED → ALERT
#   3. consecutive_failures：>= 2 → ALERT
#
# 输出：
#   - logs/sentinel/sentinel_YYYY-MM-DD.log（每天一行 JSON）
#   - ops_events 表（auth.db）
#   - 钉钉（仅当 ALERT 且 DINGTALK_WEBHOOK 已配置）
#
# 退出码：
#   - 0：健康（或 ALERT 已发送）
#   - 2：异常（status 读取失败等）

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 让 DINGTALK_WEBHOOK / DINGTALK_SECRET 可被读到（cron 不自动加载 .env）
env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)

AUTH_STATUS_PATH = Path("/var/lib/smm-collector/auth_status.json")
COLL_STATUS_PATH = Path("/var/lib/smm-collector/collector_status.json")
SENTINEL_LOG_DIR = ROOT / "logs" / "sentinel"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("smm_collector.sentinel")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        log.warning("sentinel: %s 不存在（首次启动或权限？）", path)
        return None
    except Exception as e:
        log.error("sentinel: 读取 %s 失败：%s", path, e)
        return None


def assess() -> dict[str, Any]:
    """只读评估：返回哨兵结论。"""
    now = datetime.now()
    auth = _read_json(AUTH_STATUS_PATH) or {}
    coll = _read_json(COLL_STATUS_PATH) or {}

    auth_status = auth.get("status", "UNKNOWN")
    auth_reason = auth.get("reason", "")
    consecutive_auth_failures = int(auth.get("consecutive_failures", 0) or 0)

    coll_status = coll.get("status", "UNKNOWN")
    last_success_at = coll.get("last_success_at")
    consecutive_coll_failures = int(coll.get("consecutive_failures", 0) or 0)
    latest_price_date = coll.get("latest_price_date")

    alerts = []
    is_alert = False

    is_weekday = now.weekday() < 5  # Mon-Fri
    success_today = False
    if last_success_at:
        try:
            last_dt = datetime.fromisoformat(last_success_at)
            success_today = last_dt.date() == now.date()
        except (ValueError, TypeError):
            pass

    if is_weekday and not success_today:
        catchup_deadline = now.replace(hour=10, minute=15, second=0, microsecond=0)
        if now >= catchup_deadline:
            alerts.append(
                "COLLECTOR_STALE: 今日 09:05/09:30 后无 last_success_at "
                "(status=%s, consecutive_failures=%d)"
                % (coll_status, consecutive_coll_failures)
            )
            is_alert = True

    if auth_status in ("AUTH_EXPIRED", "AUTH_VERIFICATION_REQUIRED",
                       "AUTH_LOGIN_FAILED", "AUTH_NETWORK_ERROR"):
        alerts.append("%s: %s" % (auth_status, auth_reason))
        is_alert = True
    if consecutive_auth_failures >= 2:
        alerts.append("连续认证失败 %d 次" % consecutive_auth_failures)
        is_alert = True

    if consecutive_coll_failures >= 2:
        alerts.append("连续采集失败 %d 次" % consecutive_coll_failures)
        is_alert = True

    return {
        "checked_at": now.isoformat(timespec="seconds"),
        "is_weekday": is_weekday,
        "auth_status": auth_status,
        "auth_reason": auth_reason,
        "consecutive_auth_failures": consecutive_auth_failures,
        "collector_status": coll_status,
        "last_success_at": last_success_at,
        "success_today": success_today,
        "latest_price_date": latest_price_date,
        "consecutive_coll_failures": consecutive_coll_failures,
        "alerts": alerts,
        "is_alert": is_alert,
    }


def write_sentinel_log(result):
    SENTINEL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = SENTINEL_LOG_DIR / ("sentinel_%s.log" % result["checked_at"][:10])
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


def write_ops_event(result):
    try:
        from smm_collector.ops_events import write_event
        level = "error" if result["is_alert"] else "info"
        msg = " | ".join(result["alerts"]) if result["alerts"] else "sentinel ok"
        detail = json.dumps({
            "auth_status": result["auth_status"],
            "collector_status": result["collector_status"],
            "last_success_at": result["last_success_at"],
            "consecutive_coll_failures": result["consecutive_coll_failures"],
        }, ensure_ascii=False)
        write_event(
            event_type="SENTINEL_CHECK",
            level=level,
            message=msg,
            detail=detail,
        )
    except Exception as e:
        log.warning("sentinel: 写 ops_events 失败（不影响主流程）：%s", e)


async def send_dingtalk_alert(result):
    webhook = os.environ.get("DINGTALK_WEBHOOK", "")
    if not webhook:
        log.info("sentinel: DINGTALK_WEBHOOK 未配置，跳过推送")
        return
    try:
        import httpx
        title = "⚠️ SMM 采集异常 %s" % result["checked_at"][:10]
        lines = [
            "**Auth**：`%s`" % result["auth_status"],
            "**Collector**：`%s`" % result["collector_status"],
            "**Last success**：`%s`" % (result.get("last_success_at") or "无"),
            "**Latest price_date**：`%s`" % (result.get("latest_price_date") or "无"),
            "",
            "**Alerts**:",
        ]
        lines.extend(["- %s" % a for a in result["alerts"]])
        text = "\n".join(lines)
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                webhook,
                json={"msgtype": "markdown", "markdown": {"title": title, "text": text}},
            )
            if r.status_code == 200 and r.json().get("errcode") == 0:
                log.info("sentinel: 钉钉告警已发送")
            else:
                log.warning("sentinel: 钉钉推送失败：%s %s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("sentinel: 钉钉推送异常：%s", e)


async def main():
    log.info("sentinel: 检查开始")
    result = assess()
    write_sentinel_log(result)
    write_ops_event(result)
    if result["is_alert"]:
        log.warning("sentinel: ALERT — %s", result["alerts"])
        await send_dingtalk_alert(result)
    else:
        log.info("sentinel: 健康（auth=%s, coll=%s）",
                 result["auth_status"], result["collector_status"])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
