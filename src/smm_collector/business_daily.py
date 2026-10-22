"""钉钉业务报价日报数据层（与网站「每日报价」共用同一映射与同一查询口径）。

同一映射 config/business_products.yaml（40 条 SMM 业务品种）、同一 SQLite、
同一查询函数 portal_service——绝不另起一套口径，保证网站与钉钉价格一致。

口径（2026-10-20 需求确认）：
  - 本次有效新报价 = 存在 price_date==data_date 且 average_price 非空的有效报价点，
    且该行 collected_at >= 本次采集 started_at（本次采集在页面看到并刷新过该行）。
  - 本次无新报价 = 其余情况；附 price_date < data_date 的最近真实有效报价（真实日期）。
  - 历史也无有效报价 → 「本次无新报价，历史暂无有效报价」，绝不制造数据。
  - unavailable_merged（无绑定系列）恒为「历史暂无有效报价」。

消息长度：钉钉 markdown 上限 20KB；单条超 MAX_MESSAGE_CHARS 时按业务类别
拆分为连续多条（采集概况在首条），产品零丢失。
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from . import portal_service as ps

log = logging.getLogger("smm_collector.business_daily")

STATUS_NEW = "new_quote"
STATUS_MISSING = "no_new_quote"
STATUS_NO_HISTORY = "no_history"

EXPORT_STEM = "SMM锂电业务产品报价"

EXCEL_HEADERS = ["业务类别", "产品名称", "产品规格", "最低价", "最高价", "日均价",
                 "涨跌", "单位", "实际报价日期", "当次报价状态", "最近有效报价", "最近有效报价日期"]

MAX_MESSAGE_CHARS = 18000  # 钉钉 markdown 上限 20KB，留余量

CN_NUM = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
          "十一", "十二", "十三", "十四", "十五"]

_STATUS_TEXT = {
    STATUS_NEW: "本次有效新报价",
    STATUS_MISSING: "本次无新报价",
    STATUS_NO_HISTORY: "本次无新报价，历史暂无有效报价",
}

# 钉钉日报不展示的产品（用户确认 2026-10-22：现阶段不展示；日后如需恢复由用户自行修改此列表）。
# 只按稳定 ID 过滤：绝不用 display_name（储能型与动力型同名）或 mapping_status（可能误伤未来产品）。
HIDDEN_PRODUCT_IDS = ("lfp_ess_250", "lfp_ess_240")


# ── 数据读取（复用 portal_service 口径） ─────────────────────────

def load_products(config_root: Path | str) -> tuple[list[dict], dict]:
    """加载业务映射并过滤 40 条 SMM 业务品种（与网站每日报价同源）。"""
    products, meta = ps.load_products(config_root)
    return ps.smm_products(products), meta


def _parse_ts(s: str | None) -> datetime | None:
    """ISO 时间字符串 → datetime；失败返回 None（宁可降级为「可判新报价」）。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        try:
            return datetime.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None


def _prev_day(dd: str) -> str | None:
    d = ps.parse_date(dd)
    if d is None:
        return None
    return (d - timedelta(days=1)).isoformat()


def _last_valid(con, product: dict, date_to: str | None) -> dict | None:
    """price_date < data_date 的最近真实有效报价点（同一查询口径）。"""
    if date_to is None:
        return None
    try:
        pts = ps.merge_points(ps.product_rows(con, product, date_to=date_to))
    except Exception:
        return None
    for r in reversed(pts):
        q = ps.quote_for_point(r)
        if q is not None:
            return q
    return None


def build_report(products: list[dict], con, data_date: str,
                 run_started_at: str | None) -> list[dict]:
    """构建 40 种业务产品的日报行（status + 本次报价/最近有效报价）。

    失败隔离：单产品查询异常按「历史暂无有效报价」处理并记 warning，
    绝不影响其他产品与整份日报。
    """
    started = _parse_ts(run_started_at)
    dd = str(data_date or "")
    prev_day = _prev_day(dd)
    rows: list[dict] = []
    for p in products:
        row = {
            "id": p["id"], "template_row": p["template_row"],
            "organization": p["organization"], "info_category": p["info_category"],
            "display_name": p["display_name"], "spec": p["spec"], "unit": p["unit"],
            "frequency": p["frequency"], "mapping_status": p["mapping_status"],
            "status": STATUS_NO_HISTORY, "quote": None, "last_valid": None,
        }
        try:
            if not p.get("series"):
                rows.append(row)  # unavailable_merged：无独立报价，恒无历史
                continue
            quotes = ps.latest_quote([p], con, as_of=dd or None)
            q = quotes.get(p["id"])
            if q and str(q.get("price_date") or "") == dd and q.get("average_price"):
                ca = _parse_ts(q.get("collected_at"))
                if started is None or (ca is not None and ca >= started):
                    row["status"] = STATUS_NEW
                    row["quote"] = q
                else:
                    row["status"] = STATUS_MISSING
                    row["last_valid"] = _last_valid(con, p, prev_day)
            else:
                row["status"] = STATUS_MISSING
                row["last_valid"] = _last_valid(con, p, prev_day)
            # 有绑定系列但历史无任何有效报价 → 与无绑定一样按「历史暂无有效报价」处理
            if row["status"] == STATUS_MISSING and row["last_valid"] is None:
                row["status"] = STATUS_NO_HISTORY
        except Exception:
            log.warning("业务日报查询失败 id=%s，按无报价处理", p["id"], exc_info=True)
            row["status"] = STATUS_NO_HISTORY
        rows.append(row)
    return rows


# ── 分组（info_category，按模板行序首现） ─────────────────────────

def _groups(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    order: list[str] = []
    by: dict[str, list[dict]] = {}
    for r in rows:
        cat = r["info_category"] or "未分类"
        if cat not in by:
            by[cat] = []
            order.append(cat)
        by[cat].append(r)
    return [(c, by[c]) for c in order]


# ── 消息渲染 ─────────────────────────────────────────────────

def _change_text(v: str | None) -> str:
    """涨跌文本：正值补 + 号；缺失/不可解析 → —。"""
    if v in (None, ""):
        return "—"
    d = ps.to_dec(v)
    if d is None:
        return str(v)
    if d > 0:
        return f"+{ps.fmt_price(d)}"
    return ps.fmt_price(d)


def _product_line(r: dict) -> str:
    name = r["display_name"]
    if r["spec"]:
        name = f"{name}（{r['spec']}）"
    if r["status"] == STATUS_NEW:
        q = r["quote"]
        return (f"- **{name}**：最低 {q['min_price'] or '—'} ｜ 最高 {q['max_price'] or '—'} ｜ "
                f"日均 {q['average_price'] or '—'} ｜ 涨跌 {_change_text(q.get('change_value'))} ｜ "
                f"{q.get('unit') or r['unit']} ｜ {q['price_date']}")
    if r["status"] == STATUS_MISSING and r["last_valid"]:
        lv = r["last_valid"]
        return (f"- **{name}**：本次无新报价 ｜ 最近有效报价 "
                f"{lv['average_price']} {lv.get('unit') or r['unit']}（{lv['price_date']}）")
    return f"- **{name}**：本次无新报价，历史暂无有效报价"


def _split_messages(head_lines: list[str], blocks: list[str],
                    tail_lines: list[str]) -> list[str]:
    """长度保障：超限时按业务类别块拆分为连续多条（首条含概况）。"""
    full = "\n".join(head_lines + blocks + tail_lines)
    if len(full) <= MAX_MESSAGE_CHARS:
        return [full]
    messages: list[str] = []
    cur = list(head_lines)
    for b in blocks:
        if cur and len("\n".join(cur + [b])) > MAX_MESSAGE_CHARS:
            messages.append("\n".join(cur))
            cur = []
        cur.append(b)
    if tail_lines and len("\n".join(cur + tail_lines)) <= MAX_MESSAGE_CHARS:
        cur += tail_lines
        messages.append("\n".join(cur))
    else:
        if cur:
            messages.append("\n".join(cur))
        if tail_lines:
            messages.append("\n".join(tail_lines))
    n = len(messages)
    if n <= 1:
        return messages
    return [f"（{i + 1}/{n}）\n{m}" for i, m in enumerate(messages)]


def render_dingtalk_message(meta: dict, rows: list[dict]) -> list[str]:
    """生成钉钉日报 markdown 消息（可能多条）。

    rows 为空（构建失败兜底）→ 仅采集概况，不展示产品明细。
    HIDDEN_PRODUCT_IDS 中的产品不进日报消息（仅隐藏钉钉展示，
    build_report/Excel/门户口径均不受影响）。
    """
    emoji = {"success": "✅", "partial_success": "⚠️", "failed": "❌"}.get(
        meta.get("status"), "❓")
    td = str(meta.get("target_date") or "")
    dd = str(meta.get("data_date") or td)
    head = [
        f"## {emoji} 锂电价格采集日报",
        f"**日期**：{td}" + (f"（页面数据日期 {dd}）" if dd != td else ""),
        "",
        "【采集概况】",
        "",
        f"**采集结果**：{len(meta.get('success_categories') or [])}/{len(meta.get('expected_categories') or [])} 类别成功",
        f"**全量数据**：{meta.get('total_clean_rows', 0)} 条",
    ]
    if meta.get("failed_categories"):
        head.append(f"**失败类目**：{'、'.join(str(c) for c in meta['failed_categories'][:5])}")
    if not rows:
        return ["\n".join(head)]
    rows = [r for r in rows if r["id"] not in HIDDEN_PRODUCT_IDS]
    n_new = sum(1 for r in rows if r["status"] == STATUS_NEW)
    head += ["", "【重点业务产品报价】", "",
             f"目标产品：{len(rows)} 种 ｜ 本次有效新报价：{n_new} 种 ｜ "
             f"本次无新报价：{len(rows) - n_new} 种"]
    blocks = []
    for i, (cat, items) in enumerate(_groups(rows)):
        cn = CN_NUM[i] if i < len(CN_NUM) else str(i + 1)
        block = ["", f"{cn}、{cat}（{len(items)}）"] + [_product_line(r) for r in items]
        blocks.append("\n".join(block))
    return _split_messages(head, blocks, [])


# ── Excel 导出（40 种业务产品报价） ─────────────────────────────

def _excel_row(r: dict) -> list:
    q, lv = r["quote"], r["last_valid"]
    status = _STATUS_TEXT[r["status"]]
    if r["status"] == STATUS_MISSING and lv:
        status = "本次无新报价（附最近有效报价）"
    return [
        r["info_category"], r["display_name"], r["spec"],
        q["min_price"] if q else "", q["max_price"] if q else "",
        q["average_price"] if q else "", q["change_value"] if q else "",
        (q or {}).get("unit") or r["unit"],
        q["price_date"] if q else "",
        status,
        lv["average_price"] if lv else "",
        lv["price_date"] if lv else "",
    ]


def _style_sheet(ws) -> None:
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for col in ws.columns:
        width = min(60, max(12, max(len(str(c.value or "")) for c in col) + 2))
        ws.column_dimensions[col[0].column_letter].width = width


def export_quotes_xlsx(rows: list[dict], export_root: Path,
                       target_date: date) -> Path | None:
    """写 40 种业务产品报价 Excel（与每日汇总同目录同命名规则）。

    无新报价产品保留在导出结果中：价格四列留空，状态与最近有效报价如实填写。
    写临时文件后原子替换，失败不残留 .tmp。
    """
    td = target_date.isoformat() if hasattr(target_date, "isoformat") else str(target_date)
    out_dir = export_root / td[:4] / td[5:7] / "每日汇总" / "Excel"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{EXPORT_STEM}_{td}.xlsx"
    tmp = path.with_suffix(".tmp.xlsx")
    try:
        wb = Workbook()
        ws = wb.active
        ws.title = "40种业务产品报价"
        ws.append(EXCEL_HEADERS)
        for r in rows:
            ws.append(_excel_row(r))
        _style_sheet(ws)
        wb.save(tmp)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return path
