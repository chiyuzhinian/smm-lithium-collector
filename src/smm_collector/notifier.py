"""钉钉通知模块。采集完成后发送日报摘要 + 两个Excel下载链接。"""
from __future__ import annotations
import base64, hashlib, hmac, logging, os, time
from datetime import datetime
from urllib.parse import quote_plus, quote
import httpx

log = logging.getLogger("smm_collector.notify")
DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")

def _signed_url():
    if not DINGTALK_SECRET: return DINGTALK_WEBHOOK
    ts = str(round(time.time() * 1000))
    h = hmac.new(DINGTALK_SECRET.encode(), f"{ts}\n{DINGTALK_SECRET}".encode(), hashlib.sha256).digest()
    s = quote_plus(base64.b64encode(h))
    return f"{DINGTALK_WEBHOOK}&timestamp={ts}&sign={s}"

async def send_dingtalk(title, text):
    if not DINGTALK_WEBHOOK: return False
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(_signed_url(), json={"msgtype":"markdown","markdown":{"title":title,"text":text}})
            if r.status_code == 200 and r.json().get("errcode") == 0:
                log.info("dingtalk ok"); return True
    except Exception: log.exception("dingtalk error")
    return False

def _dl_url(td, filename):
    """生成文件下载链接。优先 FILE_HOST 环境变量，其次 ngrok 自动检测。"""
    fh = os.getenv("FILE_HOST", "")
    if not fh:
        # 尝试 ngrok 本地 API
        try:
            import urllib.request, json as _j
            r = urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=3)
            data = _j.loads(r.read())
            for t in data.get("tunnels", []):
                if t.get("proto") == "https":
                    fh = t["public_url"]; break
        except: pass
    if not fh:
        # 回退：局域网 IP
        try:
            import socket; s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80)); fh = f"http://{s.getsockname()[0]}:8888"; s.close()
        except: pass
    if fh:
        path = quote(f"{td[:4]}/{td[5:7]}/每日汇总/Excel/{filename}", safe="/")
        return f"{fh}/{path}"
    return ""

def build_report_message(meta, sync_stats=None):
    s = meta.get("status","unknown")
    e = {"success":"✅","partial_success":"⚠️","failed":"❌"}
    td = meta.get("target_date","")
    lines = [
        f"## {e.get(s,'❓')} SMM锂电现货采集日报",
        f"**日期**：{td} | **状态**：{s}",
        f"**分类**：{len(meta.get('success_categories',[]))}/{len(meta.get('expected_categories',[]))} 成功",
        f"**数据**：{meta.get('total_clean_rows',0)} 条",
    ]
    if meta.get("failed_categories"):
        lines.append(f"**失败**：{'、'.join(meta['failed_categories'][:5])}")
    if sync_stats:
        lines.append(f"**MySQL**：新增{sync_stats.get('inserted',0)} 更新{sync_stats.get('updated',0)}")

    # 两个下载链接
    dl1 = _dl_url(td, f"SMM锂电现货价格_{td}.xlsx")
    dl2 = _dl_url(td, f"SMM锂电现货价格_近三日对比_{td}.xlsx")
    if dl1:
        lines.append(f"\n📥 [下载当日全部数据]({dl1})")
    if dl2:
        lines.append(f"📥 [下载近三日对比及均价]({dl2})")
    lines.append(f"\n⏰ {datetime.now().strftime('%H:%M')}")
    return "\n".join(lines)

async def send_daily_notification(meta, sync_stats=None, db_path=None):
    if not DINGTALK_WEBHOOK: return
    t = f"SMM锂电{'成功' if meta.get('status')=='success' else '异常'} {meta.get('target_date','')}"
    await send_dingtalk(t, build_report_message(meta, sync_stats))
