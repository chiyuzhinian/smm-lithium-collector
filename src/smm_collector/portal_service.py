"""门户业务品种数据服务层（每日报价 / 价格走势 / 月度业务报表共用）。

设计原则（2026-09-06 需求确认）：
  - 业务品种 → 数据库报价系列的绑定只来自 config/business_products.yaml，
    身份 = (category, product_name, specification) 精确三元组，绝不用模糊名称匹配；
    record_hash 是记录哈希，不是跨日期产品 ID。
  - 同 (product_name, specification, unit, price_date) 跨分类重复收录只计一次；
    同报价点重复采集不增权重（按 price_date 去重，取 collected_at 最新）。
  - 日均价 = 某一报价日期的 average_price 源字段，绝不前端或本层重算；
    average_price 缺失显示空，不擅自用 (min+max)/2 补算。
  - 月均价 = 所选自然月内有效报价 average_price 的算术平均（按有效发布日计），
    周报价按当月实际发布报价点计；排除 validation_status='invalid' 与无法解析的数值，
    不因周频日期较旧而机械排除 warning。
  - 环比 =（本月月均价−上月月均价）÷上月月均价×100%，仅相邻两月均完整且上月月均价
    非零时计算；完整性按品种频率 + 实际缺口检查（不是只看月初月末是否有数据）。
  - 计算全程用 Decimal，展示精度在最后一步决定；元/Wh 等小数值不取整。
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import yaml

# 业务映射中周频品种的周报价基准日：SMM 电芯周报价发布于周五（数据库实证：07-17/07-24/…/08-28 均为周五）
WEEKLY_REFERENCE_WEEKDAY = 4  # Friday

# 门户日期范围下限（采集起点，见 business_products.yaml meta.history_from）
DEFAULT_HISTORY_FROM = "2026-07-20"

# 环比百分比的舍入小数位（如 1.23%）
MOM_PCT_PLACES = 2
# 月均价舍入：数值 ≥1 → 2 位小数（元/吨等）；数值 <1 → 4 位小数（元/Wh 等）
MONTHLY_AVG_PLACES_BIG = 2
MONTHLY_AVG_PLACES_SMALL = 4


# ── 配置加载 ────────────────────────────────────────────────

def load_products(config_root: Path | str) -> tuple[list[dict], dict]:
    """加载并校验业务品种映射。返回 (products 按模板行序, meta)。

    校验：id 唯一非空；template_row 为正整数；mapping_status 合法；
    series 三元组必须 (category|categories, product_name, specification) 齐全。
    非法条目跳过并记录到 meta["load_errors"]。
    """
    path = Path(config_root) / "business_products.yaml"
    meta: dict = {"history_from": DEFAULT_HISTORY_FROM,
                  "daily_completeness_ratio": 0.8,
                  "weekly_completeness_ratio": 0.75,
                  "load_errors": []}
    if not path.exists():
        meta["load_errors"].append(f"映射配置不存在: {path}")
        return [], meta
    try:
        with path.open(encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001 — 配置损坏必须显式报错而非静默
        meta["load_errors"].append(f"映射配置解析失败: {e}")
        return [], meta

    cfg_meta = cfg.get("meta") or {}
    meta.update({k: v for k, v in cfg_meta.items() if k not in ("load_errors",)})

    products: list[dict] = []
    seen_ids: set[str] = set()
    for raw in cfg.get("products") or []:
        pid = str(raw.get("id") or "").strip()
        row = int(raw.get("template_row") or 0)
        status = str(raw.get("mapping_status") or "")
        if not pid or pid in seen_ids:
            meta["load_errors"].append(f"跳过条目：id 缺失或重复 {pid!r} (row={row})")
            continue
        if row <= 0:
            meta["load_errors"].append(f"跳过条目 {pid}：template_row 非法")
            continue
        if status not in ("confirmed", "confirmed_renamed", "unavailable_merged", "manual"):
            meta["load_errors"].append(f"跳过条目 {pid}：mapping_status 非法 {status!r}")
            continue
        seen_ids.add(pid)
        series = _normalize_series(raw.get("series") or [], pid, meta)
        products.append({
            "id": pid,
            "template_row": row,
            "organization": str(raw.get("organization") or ""),
            "material_attribute": str(raw.get("material_attribute") or ""),
            "info_category": str(raw.get("info_category") or ""),
            "chemistry": str(raw.get("chemistry") or ""),
            "display_name": str(raw.get("display_name") or ""),
            "template_detail": str(raw.get("template_detail") or ""),
            "spec": str(raw.get("spec") or ""),
            "spec_source": str(raw.get("spec_source") or "template"),
            "unit": str(raw.get("unit") or ""),
            "source": str(raw.get("source") or ""),
            "frequency": str(raw.get("frequency") or ""),
            "mapping_status": status,
            "mapping_basis": str(raw.get("mapping_basis") or ""),
            "note": str(raw.get("note") or ""),
            "keywords": [str(k) for k in (raw.get("keywords") or [])],
            "series": series,
        })
    products.sort(key=lambda p: p["template_row"])
    return products, meta


def _normalize_series(raw_series: list, pid: str, meta: dict) -> list[dict]:
    """series 条目归一：category 或 categories（列表）→ 展开为多个三元组。"""
    out: list[dict] = []
    for s in raw_series or []:
        cats = s.get("categories") or ([s.get("category")] if s.get("category") else [])
        pn = str(s.get("product_name") or "").strip()
        spec = str(s.get("specification") or "")
        if not cats or not pn:
            meta["load_errors"].append(f"条目 {pid}：series 三元组不完整 {s!r}")
            continue
        for cat in cats:
            out.append({
                "category": str(cat),
                "product_name": pn,
                "specification": spec,
                "valid_from": str(s.get("valid_from") or "") or None,
                "valid_to": str(s.get("valid_to") or "") or None,
                "note": str(s.get("note") or ""),
            })
    return out


def smm_products(products: list[dict]) -> list[dict]:
    """仅 SMM 来源的 40 条业务品种（每日报价/走势范围）。"""
    return [p for p in products if p.get("source") == "SMM"]


# ── Decimal 工具 ────────────────────────────────────────────

def to_dec(v) -> Decimal | None:
    """TEXT 价格字段 → Decimal；None/NaN/不可解析 → None。"""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        d = Decimal(s)
    except Exception:  # noqa: BLE001
        return None
    return d if d.is_finite() else None


def dec_places(v: Decimal | None) -> int:
    """源数值的小数位数（用于按源精度展示日均价/涨跌）。"""
    if v is None:
        return 0
    t = v.as_tuple()
    return max(0, -t.exponent)


def fmt_price(v: Decimal | None, places: int | None = None) -> str | None:
    """价格格式化：places=None 按源精度；否则固定位数（末尾不补零按源精度时）。"""
    if v is None:
        return None
    if places is None:
        p = min(dec_places(v), 6)
        return format(v.quantize(Decimal(1).scaleb(-p)), "f")
    return format(v.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP), "f")


def round_monthly_avg(v: Decimal | None) -> Decimal | None:
    """月均价舍入：≥1 → 2 位小数；<1 → 4 位小数（元/Wh 不取整）。"""
    if v is None:
        return None
    places = MONTHLY_AVG_PLACES_BIG if abs(v) >= 1 else MONTHLY_AVG_PLACES_SMALL
    return v.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def round_mom_pct(frac: Decimal | None) -> Decimal | None:
    """环比分数 → 百分比（×100）保留 2 位小数。"""
    if frac is None:
        return None
    return (frac * 100).quantize(Decimal(1).scaleb(-MOM_PCT_PLACES), rounding=ROUND_HALF_UP)


# ── 日期工具 ────────────────────────────────────────────────

def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def month_bounds(month: str) -> tuple[date, date] | None:
    """'YYYY-MM' → (当月1日, 当月最后一日)。非法返回 None。"""
    try:
        first = datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        return None
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, nxt - timedelta(days=1)


def weekdays_in_month(first: date, last: date) -> int:
    n = 0
    d = first
    while d <= last:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def reference_weekdays_in_month(first: date, last: date, weekday: int) -> int:
    """当月指定星期几（如周五）的天数（周报价发布基准）。"""
    n = 0
    d = first
    while d <= last:
        if d.weekday() == weekday:
            n += 1
        d += timedelta(days=1)
    return n


def month_is_ended(month: str, today: date | None = None) -> bool:
    today = today or date.today()
    bounds = month_bounds(month)
    return bounds is not None and today > bounds[1]


# ── 数据库查询与合并 ────────────────────────────────────────

def open_readonly(db_path: Path | str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def product_rows(con: sqlite3.Connection, product: dict, date_from: str | None = None,
                 date_to: str | None = None) -> list[dict]:
    """按 series 三元组（OR）查询该业务品种全部合法行（排除 invalid）。

    返回原始行 dict；跨分类/跨改名对的重复由 merge_points 去重。
    """
    series = product.get("series") or []
    if not series:
        return []
    conds, params = [], []
    for t in series:
        conds.append("(category = ? AND product_name = ? AND specification = ?)")
        params.extend([t["category"], t["product_name"], t["specification"]])
    sql = ("SELECT * FROM lithium_spot_prices WHERE validation_status != 'invalid' "
           f"AND ({' OR '.join(conds)})")
    if date_from:
        sql += " AND price_date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND price_date <= ?"
        params.append(date_to)
    sql += " ORDER BY price_date, collected_at DESC, id DESC"
    try:
        return [dict(r) for r in con.execute(sql, tuple(params)).fetchall()]
    except sqlite3.Error:
        return []


def merge_points(rows: list[dict]) -> list[dict]:
    """跨分类/跨改名对合并为报价点序列：按 price_date 去重（同报价点重复采集不增权重）。

    同一 price_date 取 collected_at 最新行（已按 collected_at DESC 排序，首个即最新）；
    全部字段（min/max/avg/change/unit）来自同一条对应记录——同一行四价同源。
    """
    pts: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        d = str(r.get("price_date") or "")
        if not d or d in seen:
            continue
        seen.add(d)
        pts.append(r)
    pts.sort(key=lambda r: str(r["price_date"]))
    return pts


def point_payload(r: dict) -> dict:
    """报价点 → API 字段（数值按源精度转字符串；解析失败留空）。"""
    return {
        "price_date": str(r.get("price_date") or ""),
        "collected_at": str(r.get("collected_at") or ""),
        "min_price": fmt_price(to_dec(r.get("min_price"))),
        "max_price": fmt_price(to_dec(r.get("max_price"))),
        "average_price": fmt_price(to_dec(r.get("average_price"))),
        "change_value": fmt_price(to_dec(r.get("change_value"))),
        "unit": str(r.get("unit") or ""),
        "category": str(r.get("category") or ""),
        "product_name": str(r.get("product_name") or ""),
        "specification": str(r.get("specification") or ""),
        "market": str(r.get("market") or ""),
        "validation_status": str(r.get("validation_status") or ""),
    }


def quote_for_point(r: dict) -> dict | None:
    """报价点 → 每日报价行 quote；average_price 为空的点只作历史点，不作最新报价。"""
    avg = to_dec(r.get("average_price"))
    if avg is None:
        return None
    return point_payload(r)


# ── 每日报价（as-of 语义） ─────────────────────────────────

def latest_quote(products: list[dict], con: sqlite3.Connection, as_of: str | None = None,
                 history_from: str | None = None) -> dict[str, dict | None]:
    """每个业务品种在 as_of（含当日）之前的最新报价点。

    绝不返回所选日期之后的价格；返回实际 price_date 与 collected_at（不伪装成当日发布）。
    未绑定（unavailable_merged/manual）→ None。
    """
    result: dict[str, dict | None] = {}
    for p in products:
        rows = product_rows(con, p, date_from=history_from, date_to=as_of)
        pts = merge_points(rows)
        for r in reversed(pts):  # 最新在前
            q = quote_for_point(r)
            if q is not None:
                result[p["id"]] = q
                break
        else:
            result[p["id"]] = None
    return result


def quote_rows_payload(products: list[dict], con: sqlite3.Connection,
                       as_of: str | None = None, meta: dict | None = None) -> dict:
    """GET /api/portal/quotes 响应体：40 条 SMM 业务行的最新报价。"""
    meta = meta or {}
    smm = smm_products(products)
    quotes = latest_quote(smm, con, as_of=as_of, history_from=meta.get("history_from"))
    latest = _db_latest_date(con)
    rows = []
    for p in smm:
        q = quotes.get(p["id"])
        rows.append({
            "id": p["id"],
            "template_row": p["template_row"],
            "organization": p["organization"],
            "material_attribute": p["material_attribute"],
            "info_category": p["info_category"],
            "chemistry": p["chemistry"],
            "display_name": p["display_name"],
            "spec": p["spec"],
            "template_detail": p["template_detail"],
            "spec_source": p["spec_source"],
            "unit": p["unit"],
            "source": p["source"],
            "frequency": p["frequency"],
            "mapping_status": p["mapping_status"],
            "mapping_basis": p["mapping_basis"],
            "note": p["note"],
            "series": [{"category": t["category"], "product_name": t["product_name"],
                        "specification": t["specification"]} for t in p["series"]],
            "quote": q,
        })
    return {
        "meta": {"generated_at": datetime.now().isoformat(timespec="seconds"),
                 "source": "sqlite",
                 "as_of": as_of,
                 "db_latest_date": latest,
                 "history_from": meta.get("history_from")},
        "rows": rows,
    }


def _db_latest_date(con: sqlite3.Connection) -> str | None:
    try:
        r = con.execute(
            "SELECT MAX(price_date) AS d FROM lithium_spot_prices "
            "WHERE price_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'").fetchone()
    except sqlite3.Error:
        return None
    return str(r["d"]) if r and r["d"] else None


def product_meta_payload(products: list[dict], meta: dict | None = None,
                         con: sqlite3.Connection | None = None) -> dict:
    """GET /api/portal/products 响应体：全部业务品种（49 行）+ 筛选维度。"""
    meta = meta or {}
    smm = smm_products(products)
    orgs, cats, sources, freqs = [], [], [], []
    for p in products:
        if p["organization"] and p["organization"] not in orgs:
            orgs.append(p["organization"])
        if p["info_category"] and p["info_category"] not in cats:
            cats.append(p["info_category"])
        if p["source"] and p["source"] not in sources:
            sources.append(p["source"])
        if p["frequency"] and p["frequency"] not in freqs:
            freqs.append(p["frequency"])
    db_latest = _db_latest_date(con) if con is not None else None
    return {
        "meta": {"generated_at": datetime.now().isoformat(timespec="seconds"),
                 "history_from": meta.get("history_from"),
                 "db_latest_date": db_latest,
                 "method_note": meta.get("monthly_method_note", ""),
                 "load_errors": meta.get("load_errors", [])},
        "filters": {"organizations": orgs, "info_categories": cats, "sources": sources},
        "products": [{
            "id": p["id"], "template_row": p["template_row"],
            "organization": p["organization"], "material_attribute": p["material_attribute"],
            "info_category": p["info_category"], "chemistry": p["chemistry"],
            "display_name": p["display_name"], "spec": p["spec"],
            "template_detail": p["template_detail"],
            "spec_source": p["spec_source"], "unit": p["unit"], "source": p["source"],
            "frequency": p["frequency"], "mapping_status": p["mapping_status"],
            "mapping_basis": p["mapping_basis"], "note": p["note"],
            "keywords": p["keywords"],
            "series": [{"category": t["category"], "product_name": t["product_name"],
                        "specification": t["specification"], "valid_from": t["valid_from"],
                        "valid_to": t["valid_to"]} for t in p["series"]],
            "searchable": _searchable_text(p),
        } for p in products],
        "smm_count": len(smm),
        "total_count": len(products),
    }


def _searchable_text(p: dict) -> str:
    parts = [p["display_name"], p["spec"], p["chemistry"], p["organization"],
             p["info_category"], p["source"]] + p["keywords"]
    for t in p["series"]:
        parts.extend([t["category"], t["product_name"], t["specification"]])
    return " ".join(x for x in parts if x)


def search_products(products: list[dict], q: str) -> list[dict]:
    """产品搜索：业务名称 / SMM 名称 / 常用缩写（大小写与全半角不敏感）。"""
    q = _norm_search(q)
    if not q:
        return products
    return [p for p in products if q in _norm_search(_searchable_text(p))]


def _norm_search(s: str) -> str:
    """搜索归一：小写、去空白、常见全半角/缩写归一。"""
    t = str(s).lower()
    for full, half in (("（", "("), ("）", ")"), ("，", ","), ("．", "."), ("／", "/"), ("　", " ")):
        t = t.replace(full, half)
    return " ".join(t.split())


# ── 历史序列（走势共用） ────────────────────────────────────

def history_payload(products: list[dict], con: sqlite3.Connection, ids: list[str],
                    date_from: str | None = None, date_to: str | None = None,
                    meta: dict | None = None) -> dict:
    """GET /api/portal/history 响应体：统一日期窗口内每个业务品种的合并序列。

    真实时间轴：只含 DB 真实报价点，不插值、缺报不填 0；
    同一日期区间对所有品种一致（不按各自最新日期移动窗口）。
    """
    meta = meta or {}
    meta_from = meta.get("history_from")
    if date_from is None or (meta_from and date_from < meta_from):
        date_from = meta_from
    by_id = {p["id"]: p for p in smm_products(products)}
    wanted = [by_id[i] for i in ids if i in by_id]
    series_out = []
    for p in wanted:
        pts = merge_points(product_rows(con, p, date_from=date_from, date_to=date_to))
        series_out.append({
            "id": p["id"], "display_name": p["display_name"], "spec": p["spec"],
            "unit": p["unit"], "frequency": p["frequency"],
            "mapping_status": p["mapping_status"], "note": p["note"],
            "points": [point_payload(r) for r in pts],
        })
    return {
        "meta": {"generated_at": datetime.now().isoformat(timespec="seconds"),
                 "source": "sqlite", "date_from": date_from, "date_to": date_to},
        "series": series_out,
    }


# ── 月均价 / 完整性 / 环比 ──────────────────────────────────

def collection_calendar(con: sqlite3.Connection, month: str, products: list[dict],
                        frequency: str) -> list[str]:
    """某频次（daily|weekly）业务品种在当月的采集日历 = 全部该频次已绑定品种报价日并集。"""
    bounds = month_bounds(month)
    if bounds is None:
        return []
    first, last = bounds
    m_from, m_to = first.isoformat(), last.isoformat()
    dates: set[str] = set()
    for p in products:
        if p.get("frequency") != frequency or not p.get("series"):
            continue
        for r in product_rows(con, p, date_from=m_from, date_to=m_to):
            d = str(r.get("price_date") or "")[:10]
            if d:
                dates.add(d)
    return sorted(dates)


def monthly_stats(product: dict, con: sqlite3.Connection, month: str,
                  meta: dict | None = None, all_products: list[dict] | None = None) -> dict:
    """单品种月度统计：月均价 + 参与报价点 + 完整性判定 + 依据。

    all_products：完整产品表（计算采集日历需要；缺省时用月度预览注入的全量表）。
    返回 {monthly_avg(Decimal|None), points:[point_payload], n_points,
          date_from, date_to, completeness: complete|incomplete|in_progress|unavailable|manual,
          reason}。
    """
    meta = meta or {}
    bounds = month_bounds(month)
    if bounds is None:
        return {"monthly_avg": None, "points": [], "n_points": 0, "date_from": None,
                "date_to": None, "completeness": "unavailable", "reason": "月份格式非法"}
    first, last = bounds
    m_from, m_to = first.isoformat(), last.isoformat()

    status = product.get("mapping_status")
    if status == "manual":
        return {"monthly_avg": None, "points": [], "n_points": 0, "date_from": None,
                "date_to": None, "completeness": "manual",
                "reason": "非SMM来源，保留原行供人工填写"}
    if status == "unavailable_merged":
        return {"monthly_avg": None, "points": [], "n_points": 0, "date_from": None,
                "date_to": None, "completeness": "unavailable",
                "reason": product.get("note") or "暂无数据"}

    pts = merge_points(product_rows(con, product, date_from=m_from, date_to=m_to))
    vals: list[Decimal] = []
    used: list[dict] = []
    for r in pts:
        v = to_dec(r.get("average_price"))
        if v is None:
            continue
        vals.append(v)
        used.append(r)
    avg = (sum(vals, Decimal(0)) / Decimal(len(vals))) if vals else None
    avg = round_monthly_avg(avg)

    ended = month_is_ended(month)
    freq = product.get("frequency") or "daily"
    if not ended:
        comp, reason = "in_progress", "月份未结束，以下为截至当前的期间均价"
    elif not vals:
        comp, reason = "incomplete", "当月无有效报价"
    elif freq == "weekly":
        comp, reason = _weekly_completeness(month, meta, len(vals), first, last)
    else:
        comp, reason = _daily_completeness(month, con, meta, len(vals), first, last,
                                           all_products)

    return {
        "monthly_avg": avg,
        "points": [point_payload(r) for r in used],
        "n_points": len(vals),
        "date_from": pts[0]["price_date"] if pts else None,
        "date_to": pts[-1]["price_date"] if pts else None,
        "completeness": comp,
        "reason": reason,
    }


def _daily_completeness(month: str, con: sqlite3.Connection, meta: dict,
                        n_points: int, first: date, last: date,
                        all_products: list[dict] | None) -> tuple[str, str]:
    """日频完整性：两级检查（采集日历自身完整性 + 品种对日历覆盖率）。

    all_products 为 None（无全量产品表）时退化为工作日比例单级检查。
    """
    ratio = float(meta.get("daily_completeness_ratio", 0.8))
    weekdays = weekdays_in_month(first, last)
    if all_products:
        cal = collection_calendar(con, month, all_products, "daily")
        cal_days = len(cal)
        if weekdays and cal_days / weekdays < ratio:
            return "incomplete", (f"当月采集日历不完整：采集日{cal_days}/{weekdays}个工作日"
                                  f"（<{ratio:.0%}），无法判定品种完整性")
        if cal_days and n_points / cal_days < ratio:
            return "incomplete", (f"品种当月报价日{n_points}/{cal_days}个采集日"
                                  f"（<{ratio:.0%}），存在实际缺口")
        if not cal_days and not n_points:
            return "incomplete", "当月无采集数据"
        return "complete", f"当月报价日 {n_points}/{cal_days or n_points} 个采集日，覆盖完整"
    if weekdays and n_points / weekdays < ratio:
        return "incomplete", (f"品种当月报价日{n_points}/{weekdays}个工作日"
                              f"（<{ratio:.0%}），存在实际缺口")
    if not n_points:
        return "incomplete", "当月无有效报价"
    return "complete", f"当月报价日 {n_points}/{weekdays} 个工作日，覆盖完整"


def _weekly_completeness(month: str, meta: dict, n_points: int,
                         first: date, last: date) -> tuple[str, str]:
    """周频完整性：当月实际发布报价点数 vs 当月周数（周五基准）。"""
    ratio = float(meta.get("weekly_completeness_ratio", 0.75))
    fridays = reference_weekdays_in_month(first, last, WEEKLY_REFERENCE_WEEKDAY)
    if fridays and n_points / fridays < ratio:
        return "incomplete", (f"品种当月周报价{n_points}点/{fridays}个周五"
                              f"（<{ratio:.0%}），存在实际缺口")
    if fridays == 0:
        return "incomplete", "当月无周报价发布基准日"
    return "complete", f"当月周报价 {n_points}/{fridays} 点，覆盖完整"


def monthly_payload(products: list[dict], con: sqlite3.Connection, month: str,
                    meta: dict | None = None) -> dict:
    """GET /api/portal/monthly 响应体：49 行业务月报预览（主表口径）。"""
    meta = meta or {}
    bounds = month_bounds(month)
    if bounds is None:
        return {"meta": {}, "error": "月份格式非法（应为 YYYY-MM）"}
    first, last = bounds
    prev_month = (first.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    ended = month_is_ended(month)

    cal_daily = collection_calendar(con, month, products, "daily")
    cal_weekly = collection_calendar(con, month, products, "weekly")
    rows = []
    for p in products:
        st = monthly_stats(p, con, month, meta, all_products=products)
        prev_st = monthly_stats(p, con, prev_month, meta, all_products=products)
        mom_pct, mom_reason = None, None
        if p.get("mapping_status") in ("confirmed", "confirmed_renamed"):
            cur, prv = st.get("monthly_avg"), prev_st.get("monthly_avg")
            if cur is not None and prv is not None and prv != 0:
                if st["completeness"] == "complete" and prev_st["completeness"] == "complete":
                    mom_pct = round_mom_pct((cur - prv) / prv)
                elif st["completeness"] == "in_progress":
                    mom_reason = "本月未结束，环比不计算"
                else:
                    mom_reason = _mom_block_reason(st, prev_st, prev_month)
            elif prv is None or prv == 0:
                mom_reason = "上月月均价缺失或为0，不计算环比"
            elif cur is None:
                mom_reason = "本月无有效报价，不计算环比"
        rows.append({
            "id": p["id"], "template_row": p["template_row"],
            "organization": p["organization"], "material_attribute": p["material_attribute"],
            "info_category": p["info_category"], "chemistry": p["chemistry"],
            "display_name": p["display_name"], "spec": p["spec"],
            "template_detail": p["template_detail"],
            "unit": p["unit"], "source": p["source"],
            "mapping_status": p["mapping_status"],
            "monthly_avg": _fmt_monthly(st["monthly_avg"]),
            "n_points": st["n_points"],
            "date_from": st["date_from"], "date_to": st["date_to"],
            "completeness": st["completeness"], "reason": st["reason"],
            "prev_monthly_avg": _fmt_monthly(prev_st["monthly_avg"]),
            "prev_n_points": prev_st["n_points"],
            "prev_completeness": prev_st["completeness"],
            "mom_pct": fmt_price(mom_pct, 2) if mom_pct is not None else None,
            "mom_reason": mom_reason,
            "note": p.get("note") or "",
            "points": st["points"],
        })
    summary = {
        "month": month, "month_start": first.isoformat(), "month_end": last.isoformat(),
        "month_ended": ended,
        "total_rows": len(rows),
        "smm_rows": sum(1 for r in rows if r["source"] == "SMM"),
        "filled": sum(1 for r in rows if r["monthly_avg"] is not None),
        "blank": len(rows) - sum(1 for r in rows if r["monthly_avg"] is not None),
        "unavailable_merged": [r["id"] for r in rows if r["mapping_status"] == "unavailable_merged"],
        "manual_rows": [r["id"] for r in rows if r["mapping_status"] == "manual"],
        "mom_computed": sum(1 for r in rows if r["mom_pct"] is not None),
        "mom_blocked": sum(1 for r in rows if r["monthly_avg"] is not None
                           and r["mom_pct"] is None),
        "calendar_daily_days": len(cal_daily),
        "calendar_daily_weekdays": weekdays_in_month(first, last),
        "calendar_weekly_points": len(cal_weekly),
        "calendar_weekly_fridays": reference_weekdays_in_month(
            first, last, WEEKLY_REFERENCE_WEEKDAY),
        "method_note": meta.get("monthly_method_note", ""),
    }
    return {"meta": {"generated_at": datetime.now().isoformat(timespec="seconds"),
                     "source": "sqlite"}, "summary": summary, "rows": rows}


def _fmt_monthly(v: Decimal | None) -> str | None:
    """月均价字符串：≥1 → 2 位小数；<1 → 4 位小数。"""
    if v is None:
        return None
    places = MONTHLY_AVG_PLACES_BIG if v >= 1 else MONTHLY_AVG_PLACES_SMALL
    return fmt_price(v, places)


def _mom_block_reason(cur_st: dict, prev_st: dict, prev_month: str) -> str:
    if prev_st["completeness"] == "incomplete":
        return f"上月（{prev_month}）数据不完整（{prev_st['reason']}），环比未计算"
    if prev_st["completeness"] == "in_progress":
        return "上月未结束，环比不计算"
    if cur_st["completeness"] == "incomplete":
        return f"本月数据不完整（{cur_st['reason']}），环比未计算"
    return "口径不可比较，环比未计算"


# ── 全部分类数据（数据与报表栏目） ──────────────────────────

def dataset_categories(con: sqlite3.Connection) -> list[dict]:
    """全部采集分类（按 DB 实际数据动态生成，不硬编码样本数量）。"""
    try:
        rows = con.execute(
            "SELECT category, COUNT(*) AS row_count, COUNT(DISTINCT price_date) AS date_count, "
            "MIN(price_date) AS min_date, MAX(price_date) AS max_date "
            "FROM lithium_spot_prices WHERE validation_status != 'invalid' "
            "AND price_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' "
            "GROUP BY category ORDER BY category").fetchall()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


def dataset_quotes(con: sqlite3.Connection, category: str | None = None,
                   product: str | None = None, q: str | None = None,
                   date_from: str | None = None, date_to: str | None = None,
                   page: int = 1, page_size: int = 100,
                   categories: list[str] | None = None) -> dict:
    """全量原始行分页查询（全部分类；与业务映射无关的全量入口）。

    categories：多分类并集过滤（空列表=不限定）；category 单值保留向后兼容，
    两者同传时多分类优先。
    """
    page = max(1, page)
    page_size = min(200, max(1, page_size))
    conds = ["validation_status != 'invalid'"]
    params: list = []
    if categories:
        conds.append(f"category IN ({','.join('?' * len(categories))})")
        params.extend(categories)
    elif category:
        conds.append("category = ?")
        params.append(category)
    if product:
        conds.append("product_name = ?")
        params.append(product)
    if q:
        conds.append("(product_name LIKE ? OR specification LIKE ? OR category LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    if date_from:
        conds.append("price_date >= ?")
        params.append(date_from)
    if date_to:
        conds.append("price_date <= ?")
        params.append(date_to)
    where = " AND ".join(conds)
    try:
        total = con.execute(f"SELECT COUNT(*) AS c FROM lithium_spot_prices WHERE {where}",
                            tuple(params)).fetchone()["c"]
        rows = con.execute(
            f"SELECT * FROM lithium_spot_prices WHERE {where} "
            "ORDER BY price_date DESC, category, product_name, specification "
            "LIMIT ? OFFSET ?", tuple(params) + (page_size, (page - 1) * page_size)).fetchall()
    except sqlite3.Error:
        return {"total": 0, "page": page, "page_size": page_size, "rows": []}
    return {"total": total, "page": page, "page_size": page_size,
            "rows": [point_payload(dict(r)) for r in rows]}


def dataset_products(con: sqlite3.Connection, category: str | None = None,
                     categories: list[str] | None = None) -> list[dict]:
    """分类下的产品清单（下拉用；categories 多分类并集，空列表=全部）。"""
    conds = ["validation_status != 'invalid'"]
    params: list = []
    if categories:
        conds.append(f"category IN ({','.join('?' * len(categories))})")
        params.extend(categories)
    elif category:
        conds.append("category = ?")
        params.append(category)
    try:
        rows = con.execute(
            f"SELECT category, product_name, specification, unit, COUNT(*) AS row_count, "
            f"MIN(price_date) AS min_date, MAX(price_date) AS max_date "
            f"FROM lithium_spot_prices WHERE {' AND '.join(conds)} "
            "GROUP BY category, product_name, specification, unit "
            "ORDER BY category, product_name, specification", tuple(params)).fetchall()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]
