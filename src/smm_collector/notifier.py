"""钉钉通知模块。采集完成后发送含核心价格数据的日报到钉钉群。"""
from __future__ import annotations
import base64, hashlib, hmac, json, logging, os, sqlite3, time
from datetime import datetime
from decimal import Decimal
from urllib.parse import quote_plus
import httpx

log = logging.getLogger("smm_collector.notify")

DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")

KEY_PRODUCTS = [
    ("锂化合物", "SMM电池级碳酸锂指数"), ("锂化合物", "电池级碳酸锂"),
    ("锂化合物", "工业级碳酸锂"), ("锂化合物", "SMM电池级氢氧化锂指数"),
    ("锂化合物", "电池级氢氧化锂（粗颗粒）"), ("锂矿", "锂辉石精矿（CIF中国）指数"),
    ("锂金属", "电池级金属锂"), ("正极材料", "磷酸铁锂（动力型）"),
    ("正极材料", "三元材料523"), ("人造石墨", "低端储能人造石墨"),
    ("电解液", "电解液（三元）"), ("隔膜", "湿法隔膜"),
    ("铜箔", "锂电铜箔"), ("电芯", "方形磷酸铁锂电芯"),
    ("废旧锂电池", "废旧锂电池"),
]


def _fmt(v):
    """安全格式化数值为千分位字符串。"""
    if v is None: return "-"
    try: return f"{float(v):,.0f}"
    except Exception: return str(v)[:12]


def _dec(v):
    """转为 Decimal。"""
    if v is None: return None
    try: return Decimal(str(v))
    except Exception: return None


def _dingtalk_signed_url():
    if not DINGTALK_SECRET:
        return DINGTALK_WEBHOOK
    ts = str(round(time.time() * 1000))
    h = hmac.new(DINGTALK_SECRET.encode(), f"{ts}\n{DINGTALK_SECRET}".encode(), hashlib.sha256).digest()
    sign = quote_plus(base64.b64encode(h))
    sep = "&" if "?" in DINGTALK_WEBHOOK else "?"
    return f"{DINGTALK_WEBHOOK}{sep}timestamp={ts}&sign={sign}"


async def send_dingtalk(title: str, text: str) -> bool:
    if not DINGTALK_WEBHOOK: return False
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(_dingtalk_signed_url(), json={"msgtype": "markdown", "markdown": {"title": title, "text": text}})
            if r.status_code == 200 and r.json().get("errcode") == 0:
                log.info("钉钉通知成功"); return True
            log.warning("钉钉通知失败 %s %s", r.status_code, r.text)
    except Exception: log.exception("钉钉通知异常")
    return False


def _query_prices(db_path: str) -> tuple:
    """查询核心品种最新价格和近三日均价。返回 (latest_rows, avg3_rows)。"""
    try:
        con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
        latest = con.execute("SELECT MAX(price_date) FROM lithium_spot_prices").fetchone()[0]
        dates = [r[0] for r in con.execute("SELECT DISTINCT price_date FROM lithium_spot_prices ORDER BY price_date DESC LIMIT 3")]
        key_rows = []
        avg_rows = []
        for cat, pn in KEY_PRODUCTS:
            row = con.execute("SELECT * FROM lithium_spot_prices WHERE category=? AND product_name=? AND price_date=? LIMIT 1", (cat, pn, latest)).fetchone()
            if row: key_rows.append(dict(row))
            if len(dates) >= 2:
                placeholders = ",".join("?" * len(dates))
                prices = con.execute(f"SELECT average_price FROM lithium_spot_prices WHERE category=? AND product_name=? AND price_date IN ({placeholders}) AND validation_status!='invalid' ORDER BY price_date DESC", (cat, pn, *dates)).fetchall()
                vals = [_dec(p[0]) for p in prices if p[0] is not None]
                if vals:
                    avg_rows.append({"category": cat, "product_name": pn, "avg3": sum(vals) / len(vals), "days": len(vals)})
        con.close()
        return key_rows, avg_rows
    except Exception:
        return [], []


def build_report_message(meta: dict, sync_stats: dict | None = None, db_path: str | None = None) -> str:
    s, e = meta.get("status", "unknown"), {"success": "✅", "partial_success": "⚠️", "failed": "❌"}
    emoji = e.get(s, "❓")
    td = meta.get("target_date", "")
    lines = [f"## {emoji} SMM锂电现货采集日报", f"**日期**：{td} | **状态**：{s}"]
    lines.append(f"**分类**：{len(meta.get('success_categories',[]))}/{len(meta.get('expected_categories',[]))} | **数据**：{meta.get('total_clean_rows',0)}条")
    if sync_stats:
        lines.append(f"**MySQL**：新增{sync_stats.get('inserted',0)} 更新{sync_stats.get('updated',0)} 跳过{sync_stats.get('skipped',0)}")

    if db_path:
        key_rows, avg_rows = _query_prices(db_path)
        if key_rows:
            lines.append(f"\n#### 📊 核心品种 ({key_rows[0].get('price_date','')})")
            lines.append("| 品种 | 均价 | 涨跌 |")
            lines.append("|------|------|------|")
            for r in key_rows[:12]:
                chg = r.get("change_value")
                chg_str = ""
                if chg is not None:
                    try:
                        cv = float(chg)
                        if cv != 0: chg_str = f"{'↑' if cv > 0 else '↓'}{abs(cv):,.0f}"
                    except Exception: pass
                lines.append(f"| {r['product_name'][:18]} | {_fmt(r.get('average_price'))} | {chg_str} |")
        if avg_rows:
            lines.append(f"\n#### 📈 近三日均价 ({avg_rows[0].get('days',0)}日窗口)")
            lines.append("| 品种 | 三日均价 | 天数 |")
            lines.append("|------|---------|------|")
            for r in avg_rows[:10]:
                lines.append(f"| {r['product_name'][:18]} | {_fmt(r.get('avg3'))} | {r['days']} |")

    if meta.get("failed_categories"):
        lines.append(f"\n> ⚠️ 失败：{'、'.join(meta['failed_categories'][:5])}")
    lines.append(f"\n📁 Excel：`data/exports/{td[:4]}/{td[5:7]}/每日汇总/Excel/`")
    lines.append(f"⏰ {datetime.now().strftime('%H:%M')}")
    return "\n".join(lines)


async def send_daily_notification(meta: dict, sync_stats: dict | None = None, db_path: str | None = None):
    if not DINGTALK_WEBHOOK: return
    title = f"SMM锂电{'成功' if meta.get('status') == 'success' else '异常'} {meta.get('target_date','')}"
    await send_dingtalk(title, build_report_message(meta, sync_stats, db_path))
