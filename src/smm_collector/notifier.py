"""钉钉/企业微信通知模块。

每天采集完成后发送汇总消息到指定群。
"""
from __future__ import annotations
import base64, hashlib, hmac, json, logging, os, time
from datetime import datetime
from urllib.parse import quote_plus

import httpx

log = logging.getLogger("smm_collector.notify")

DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")


def _dingtalk_signed_url() -> str:
    """对 Webhook URL 添加签名参数。"""
    if not DINGTALK_SECRET:
        return DINGTALK_WEBHOOK
    timestamp = str(round(time.time() * 1000))
    secret_enc = DINGTALK_SECRET.encode("utf-8")
    string_to_sign = f"{timestamp}\n{DINGTALK_SECRET}"
    hmac_code = hmac.new(secret_enc, string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    sign = quote_plus(base64.b64encode(hmac_code))
    sep = "&" if "?" in DINGTALK_WEBHOOK else "?"
    return f"{DINGTALK_WEBHOOK}{sep}timestamp={timestamp}&sign={sign}"


async def send_dingtalk(title: str, text: str) -> bool:
    """发送 Markdown 消息到钉钉群机器人。"""
    if not DINGTALK_WEBHOOK:
        return False
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": text,
        },
    }
    try:
        url = _dingtalk_signed_url()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                body = resp.json()
                if body.get("errcode") == 0:
                    log.info("钉钉通知发送成功")
                    return True
            log.warning("钉钉通知失败: %s %s", resp.status_code, resp.text)
    except Exception:
        log.exception("钉钉通知异常")
    return False


def build_report_message(meta: dict, sync_stats: dict | None = None) -> str:
    """根据采集元数据生成 Markdown 格式的日报消息。"""
    status = meta.get("status", "unknown")
    status_emoji = {"success": "✅", "partial_success": "⚠️", "failed": "❌"}
    emoji = status_emoji.get(status, "❓")

    target_date = meta.get("target_date", "")
    expected = len(meta.get("expected_categories", []))
    succeeded = len(meta.get("success_categories", []))
    failed = len(meta.get("failed_categories", []))
    total_clean = meta.get("total_clean_rows", 0)

    lines = [
        f"## {emoji} SMM锂电现货采集日报",
        f"**采集日期**：{target_date}",
        f"**状态**：{status}",
        f"**分类**：{succeeded}/{expected} 成功" + (f"，{failed} 失败" if failed else ""),
        f"**数据行数**：{total_clean} 条",
    ]

    if sync_stats:
        sync_status = sync_stats.get("status", "?")
        inserted = sync_stats.get("inserted", 0)
        updated = sync_stats.get("updated", 0)
        failed_sync = sync_stats.get("failed", 0)
        lines.append(f"**MySQL同步**：{sync_status}（新增{inserted}，更新{updated}，失败{failed_sync}）")

    if meta.get("failed_categories"):
        lines.append(f"\n> 失败分类：{'、'.join(meta['failed_categories'][:5])}")

    lines.append(f"\n📎 每日 Excel：`data/exports/{target_date[:4]}/{target_date[5:7]}/SMM锂电现货价格_{target_date}.xlsx`")
    lines.append(f"\n---\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    return "\n".join(lines)


async def send_daily_notification(meta: dict, sync_stats: dict | None = None):
    """发送每日采集通知（钉钉）。"""
    if not DINGTALK_WEBHOOK:
        log.debug("未配置 DINGTALK_WEBHOOK，跳过通知")
        return

    title = f"SMM锂电采集 {'成功' if meta.get('status') == 'success' else '异常'} {meta.get('target_date', '')}"
    text = build_report_message(meta, sync_stats)
    await send_dingtalk(title, text)
