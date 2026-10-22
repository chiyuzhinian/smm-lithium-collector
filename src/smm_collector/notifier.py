"""钉钉通知模块（传输层）。业务日报文案由 business_daily.render_dingtalk_message 生成。

可靠性与防重（2026-10-20 业务日报升级）：
  - 发送失败：最多重试 3 次（5s/15s 退避），失败记 error 日志 + ops_event，
    绝不抛异常影响采集流程；
  - 防重：同一 run_id 全部消息发送成功后写 DINGTALK_REPORT_SENT 事件，
    重复调用（同一采集任务重试）前查询跳过，钉钉群不会反复收到同一份日报；
  - DINGTALK_DRY_RUN=1：只写日志不实际发送（部署验收用）。
"""
from __future__ import annotations
import asyncio, base64, hashlib, hmac, logging, os, time
from urllib.parse import quote_plus
import httpx

log = logging.getLogger("smm_collector.notify")
DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")

SEND_ATTEMPTS = 3
SEND_BACKOFF_SECONDS = [5, 15]

SENT_EVENT = "DINGTALK_REPORT_SENT"
FAILED_EVENT = "DINGTALK_SEND_FAILED"
BUILD_FAILED_EVENT = "DINGTALK_REPORT_BUILD_FAILED"

def _signed_url():
    if not DINGTALK_SECRET: return DINGTALK_WEBHOOK
    ts = str(round(time.time() * 1000))
    h = hmac.new(DINGTALK_SECRET.encode(), f"{ts}\n{DINGTALK_SECRET}".encode(), hashlib.sha256).digest()
    s = quote_plus(base64.b64encode(h))
    return f"{DINGTALK_WEBHOOK}&timestamp={ts}&sign={s}"

def _dry_run() -> bool:
    return os.getenv("DINGTALK_DRY_RUN", "").strip().lower() in ("1", "true", "yes")

async def send_dingtalk(title, text):
    """发送 markdown 消息。失败重试共 SEND_ATTEMPTS 次（退避后放弃）。"""
    if not DINGTALK_WEBHOOK: return False
    if _dry_run():
        log.info("[dry-run] 钉钉消息未实际发送 title=%s len=%d", title, len(text))
        return True
    last_err = "unknown"
    for attempt in range(SEND_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(_signed_url(), json={"msgtype":"markdown","markdown":{"title":title,"text":text}})
                if r.status_code == 200 and r.json().get("errcode") == 0:
                    log.info("dingtalk ok title=%s", title); return True
                last_err = f"status={r.status_code} body={r.text[:200]!r}"
        except Exception:
            log.exception("钉钉发送异常（第 %d 次）", attempt + 1)
            last_err = "exception"
        if attempt < SEND_ATTEMPTS - 1:
            await asyncio.sleep(SEND_BACKOFF_SECONDS[min(attempt, len(SEND_BACKOFF_SECONDS) - 1)])
    log.error("钉钉发送失败（重试 %d 次后放弃）：%s", SEND_ATTEMPTS, last_err)
    return False

async def send_daily_notification(meta, report_rows=None):
    """发送业务日报（可能拆分为多条，顺序发送）。

    report_rows=None → 仅采集概况兜底版（business_daily 处理）。
    防重：同一 run_id 已全部发送成功则跳过。
    返回 bool：全部消息发送成功。
    """
    from .business_daily import render_dingtalk_message
    from .ops_events import has_event, write_event
    if not DINGTALK_WEBHOOK and not _dry_run():
        return False
    run_id = str(meta.get("run_id") or "")
    td = str(meta.get("target_date") or "")
    try:
        if run_id and has_event(SENT_EVENT, f"run_id={run_id}"):
            log.info("钉钉日报已发送过，跳过（run_id=%s）", run_id)
            return True
    except Exception:
        pass
    try:
        texts = render_dingtalk_message(meta, report_rows or [])
    except Exception:
        log.exception("钉钉日报文案生成失败")
        try:
            write_event(BUILD_FAILED_EVENT, "error", f"run_id={run_id} target_date={td} 文案生成失败")
        except Exception:
            pass
        return False
    title = f"锂电日报 {td}"
    ok_all = True
    for i, text in enumerate(texts):
        t = title if len(texts) == 1 else f"{title}（{i+1}/{len(texts)}）"
        if not await send_dingtalk(t, text):
            ok_all = False
    try:
        if not _dry_run():  # dry-run 只是预览，不视为真实发送成功/失败
            if ok_all:
                write_event(SENT_EVENT, "info",
                            f"run_id={run_id} target_date={td} messages={len(texts)}")
            else:
                write_event(FAILED_EVENT, "error",
                            f"run_id={run_id} target_date={td} 部分消息发送失败（共{len(texts)}条）")
    except Exception:
        pass
    return ok_all
