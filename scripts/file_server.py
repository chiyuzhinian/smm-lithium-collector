"""锂电价格与回收业务数据中心 — 门户服务器。

基于 Python 标准库 http.server，无额外依赖（PyYAML 用于门户配置解析）。
功能：
  GET / /today /topics → 门户页面（static/*.html，需登录）
  GET /quality                  → 数据质量页（仅管理员）
  GET /login /register /account /admin [/admin/users]
                                → 登录 / 注册 / 账号 / 运维中心页面
  GET /static/*      → CSS / JS 静态资源（含 ECharts 本地文件，公开）
  GET /health        → 轻量健康检查（公开，无敏感信息）
  POST /api/auth/login|register → 登录 / 注册（公开；注册强制 role=user）
  POST /api/auth/logout|change-password → 登出 / 改密（登录 + CSRF）
  GET /api/auth/me|csrf         → 当前用户 / CSRF token
  GET|POST /api/admin/users     → 用户管理（列表 / 停用 / 启用 / 重置密码 / 删除）
  GET /api/admin/overview|tasks|data-quality|errors|logs|events|audit
                                → 管理员运维 API（服务端 role 校验）
  GET /api/files     → 文件列表 JSON API（30s TTL 缓存，向后兼容）
  GET /api/stats     → 统计摘要 JSON API（向后兼容）
  GET /api/overview  → 首页总览（今日必看/指标卡/专题/最近更新/固定汇总状态）
  GET /api/latest    → 今日价格页（文件/分类/完整性/涨跌榜）
  GET /api/history   → 历史数据中心（日期/分类/搜索/分页）
  GET /api/categories→ A-F 业务分组与分类清单
  GET /api/trends    → 趋势序列（SQLite 只读查询）
  GET /api/quality   → 数据质量面板（仅管理员）
  GET /api/topics    → 业务专题（回收链重点）
  GET /*             → 文件下载 / 目录浏览（exports 根内，需登录）
"""
from __future__ import annotations

import http.server
import json
import os
import re
import socketserver
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, parse_qs, quote, urlsplit

try:
    import yaml
except ImportError:  # 系统 python 无 PyYAML 时门户配置退化
    yaml = None

# ── 配置 ────────────────────────────────────────────────

PORT = int(os.getenv("FILE_SERVER_PORT", "8888"))
PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPORTS_ROOT = PROJECT_ROOT / "data" / "exports"
# 静态目录可用环境变量覆盖（开发预览用；生产默认 static/）
STATIC_ROOT = Path(os.getenv("FILE_SERVER_STATIC_DIR", str(PROJECT_ROOT / "static")))
DB_PATH = PROJECT_ROOT / "data" / "database" / "smm_lithium.db"
PORTAL_CFG = PROJECT_ROOT / "config" / "categories_portal.yaml"
# 账号库位于 systemd StateDirectory（Web 服务树之外，无法经 8888 下载）；测试用环境变量覆盖
AUTH_DB = Path(os.getenv("FILE_SERVER_AUTH_DB", "/var/lib/smm-fileserver/auth.db"))
SESSION_COOKIE = "smm_session"

# 导入 src/smm_collector 包（与 scripts/run_validation.py 同模式）
sys.path.insert(0, str(PROJECT_ROOT / "src"))

try:
    from smm_collector import ops_monitor
    from smm_collector import portal_service
    from smm_collector import monthly_report
    from smm_collector.web_auth import AuthStore
except ImportError as _import_err:  # 启动检查会拦截，fail-closed
    ops_monitor = None
    portal_service = None
    monthly_report = None
    AuthStore = None
    AUTH_IMPORT_ERROR = str(_import_err)
else:
    AUTH_IMPORT_ERROR = None

# 认证存储单例：仅在 __main__ 启动时构建（测试可注入替身）
AUTH_STORE = None

PAGE_MAP = {
    "/": "index.html",
    "/trends": "trends.html",
    "/reports": "reports.html",
    "/today": "today.html",
    "/topics": "topics.html",
    "/quality": "quality.html",
    "/login": "login.html",
    "/register": "register.html",
    "/account": "account.html",
    "/admin": "admin.html",
    "/admin/users": "admin.html",  # 运维中心「用户管理」直达（同页 tab）
}

# 公开路径（无需登录）：登录页 / 注册页 / 健康检查 / 静态资源（仅 CSS/JS/HTML，不含数据）
PUBLIC_PATHS = {"/login", "/register", "/health"}
PUBLIC_PREFIXES = ("/static/",)

# 注册节流：按 IP 计数（成功注册才计数），防止公开端点被滥用
REGISTER_MAX_PER_HOUR = 5
_register_attempts: dict[str, list[float]] = {}
_register_lock = threading.Lock()

# 安全白名单：只有这些扩展名允许直接下载
ALLOWED_EXTENSIONS = {
    ".xlsx", ".xls", ".csv", ".json", ".ndjson", ".pdf", ".txt",
    ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".zip", ".gz", ".tar",
}
# 禁止访问的文件名（精确匹配）
BLOCKED_NAMES = {
    ".env", "storage_state.json", "cookies", "auth",
    "config", ".git", "__pycache__",
}

DAILY_STEM = "SMM锂电现货价格"
DAILY_RE = re.compile(rf"^{DAILY_STEM}_(\d{{4}}-\d{{2}}-\d{{2}})\.csv$")


# ── 文件扫描 ────────────────────────────────────────────

def scan_files(root: Path, rel_prefix: str = "") -> list[dict]:
    """递归扫描目录，返回文件/目录信息列表。"""
    items = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    except PermissionError:
        return items

    for entry in entries:
        name = entry.name
        # 跳过隐藏文件、临时文件与系统中间产物（manifest/校验报告/临时快照不进文件列表）
        if name.startswith("."):
            continue
        if name.endswith(".tmp.xlsx") or name.endswith(".tmp"):
            continue
        if name.endswith(".inspect.ndjson"):
            continue
        if name.startswith("SMM数据状态_") or name.startswith("SMM数据质量报告_"):
            continue
        if "_临时" in name or name == "临时快照" or "_测试" in name:
            continue

        rel_path = (rel_prefix + "/" + name) if rel_prefix else name
        stat = entry.stat()

        item = {
            "name": name,
            "path": "/" + rel_path,
            "type": "directory" if entry.is_dir() else "file",
            "size": stat.st_size if entry.is_file() else None,
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "mtime": stat.st_mtime,
        }

        if entry.is_dir():
            # 递归获取子项数量
            children = _count_children(entry)
            item["children_count"] = children
            # 递归子目录
            sub_items = scan_files(entry, rel_path)
            items.append(item)
            items.extend(sub_items)
        else:
            items.append(item)

    return items


def _count_children(directory: Path) -> int:
    """统计目录下的一级子项数。"""
    try:
        return sum(1 for e in directory.iterdir() if not e.name.startswith("."))
    except PermissionError:
        return 0


# ── 安全校验 ────────────────────────────────────────────

def is_blocked(path: str) -> bool:
    """检查路径是否在黑名单中。"""
    parts = path.replace("\\", "/").split("/")
    for part in parts:
        if not part:
            continue
        if part in BLOCKED_NAMES:
            return True
        if part.startswith("."):
            return True
    return False


def is_safe_path(request_path: str) -> bool:
    """检查请求路径是否安全（不包含路径穿越）。"""
    normalized = os.path.normpath(request_path)
    if normalized.startswith(".."):
        return False
    if ".." in normalized.split("/"):
        return False
    return True


def _is_safe_raw(path: str) -> bool:
    """原始路径安全闸：任何路径段为 .. 或含 NUL 字节即拒绝。

    必须在 normpath 之前检查——normpath 会把 /static/../.env 折叠为 /.env，
    使穿越证据消失（这是 2026-08-13 发现的高危漏洞根因）。
    """
    if "\x00" in path:
        return False
    for seg in path.replace("\\", "/").split("/"):
        if seg == "..":
            return False
    return True


def _resolve_inside(root: Path, rel: str) -> Path | None:
    """将 rel 解析为 root 内的绝对路径；越界则返回 None。"""
    try:
        resolved = (root / rel).resolve()
    except (OSError, ValueError):
        return None
    if not resolved.is_relative_to(root.resolve()):
        return None
    return resolved


# ── 门户配置（缓存） ─────────────────────────────────────

_portal_cfg_cache = {"mtime": 0.0, "data": {}}


def _portal_config() -> dict:
    """读取 config/categories_portal.yaml，按 mtime 缓存。"""
    if yaml is None or not PORTAL_CFG.exists():
        return {}
    try:
        mtime = PORTAL_CFG.stat().st_mtime
    except OSError:
        return {}
    if _portal_cfg_cache["mtime"] != mtime:
        try:
            with PORTAL_CFG.open(encoding="utf-8") as f:
                _portal_cfg_cache["data"] = yaml.safe_load(f) or {}
            _portal_cfg_cache["mtime"] = mtime
        except Exception:
            return _portal_cfg_cache["data"] or {}
    return _portal_cfg_cache["data"]


# ── 业务品种映射（config/business_products.yaml，按 mtime 缓存） ──

BUSINESS_CFG = PROJECT_ROOT / "config" / "business_products.yaml"
_business_cache = {"mtime": 0.0, "products": [], "meta": {}}


def _business_products() -> tuple[list[dict], dict]:
    """业务品种映射（首页/走势/月报共用同一份，与服务层同源）。"""
    if portal_service is None:
        return [], {}
    try:
        mtime = BUSINESS_CFG.stat().st_mtime
    except OSError:
        return [], {}
    if _business_cache["mtime"] != mtime:
        products, meta = portal_service.load_products(PROJECT_ROOT / "config")
        _business_cache.update({"mtime": mtime, "products": products, "meta": meta})
    return _business_cache["products"], _business_cache["meta"]


def _group_map() -> dict:
    """分类名 → 业务分组映射。"""
    cfg = _portal_config()
    gm = {}
    for gk, gv in (cfg.get("groups") or {}).items():
        for c in gv.get("categories", []):
            gm[c] = {"key": gk, "name": gv.get("name", ""), "icon": gv.get("icon", "")}
    return gm


# ── 门户鉴权（表单登录 + 服务端 Session，见 DataCenterHandler） ──
# 账号/会话/锁定/审计逻辑在 smm_collector.web_auth.AuthStore，
# 配置在 categories_portal.yaml 的 auth 段；密码只存 PBKDF2 哈希。


# ── 只读数据库层 ────────────────────────────────────────

def _db_conn() -> sqlite3.Connection:
    """只读 SQLite 连接，locked 时重试。"""
    last = None
    for attempt in range(3):
        try:
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.OperationalError as e:
            last = e
            if "locked" not in str(e).lower() or attempt == 2:
                raise
            time.sleep(0.3)
    raise last


def _db_query(sql: str, params: tuple = ()) -> list[dict]:
    """执行只读查询，返回 dict 列表；数据库不可用时返回空列表。"""
    try:
        with _db_conn() as con:
            return [dict(r) for r in con.execute(sql, params).fetchall()]
    except Exception:
        return []


def _num(v) -> float | None:
    """安全数值转换（TEXT 价格字段 → float，NaN/None → None）。"""
    if v is None:
        return None
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _fmt_ts(v) -> str | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(v)


# ── 数据聚合辅助 ────────────────────────────────────────

def _scan_cached() -> list[dict]:
    """带 30s TTL 的 exports 全树扫描缓存。"""
    now = time.time()
    if _scan_cache["data"] is None or now - _scan_cache["ts"] > 30:
        _scan_cache["data"] = scan_files(EXPORTS_ROOT)
        _scan_cache["ts"] = now
    return _scan_cache["data"]


_scan_cache: dict = {"ts": 0.0, "data": None}


def _daily_dates_from_exports() -> list[str]:
    """从 exports 每日汇总 CSV 文件名提取全部日期（降序）。"""
    dates = set()
    try:
        for f in EXPORTS_ROOT.glob("*/*/每日汇总/CSV/*.csv"):
            m = DAILY_RE.match(f.name)
            if m:
                dates.add(m.group(1))
    except OSError:
        pass
    return sorted(dates, reverse=True)


def _db_latest_date() -> str | None:
    """SQLite 最大 price_date；只认 YYYY-MM-DD，避免 ''/NULL/'None' 等脏值污染。"""
    rows = _db_query(
        "SELECT MAX(price_date) AS d FROM lithium_spot_prices "
        "WHERE price_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'")
    d = rows[0]["d"] if rows else None
    return d or None


def _latest_date() -> str | None:
    """最新数据日期：优先 SQLite 最大 price_date（真实数据日期）；
    DB 无数据时回退 exports 每日文件最新日期。

    周一上午页面仍是上周五数据时，门户应展示周五而非运行日文件名，
    避免「当日价格尚未发布」式误导展示。"""
    db_latest = _db_latest_date()
    if db_latest:
        return db_latest
    export_dates = _daily_dates_from_exports()
    return export_dates[0] if export_dates else None


def _daily_files(date_str: str) -> dict:
    """某日期的每日总表文件信息（xlsx/csv 路径 + 存在性）。"""
    y, m = date_str[:4], date_str[5:7]
    xlsx = EXPORTS_ROOT / y / m / "每日汇总" / "Excel" / f"{DAILY_STEM}_{date_str}.xlsx"
    csv = EXPORTS_ROOT / y / m / "每日汇总" / "CSV" / f"{DAILY_STEM}_{date_str}.csv"
    updated = None
    for f in (xlsx, csv):
        if f.exists():
            ts = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            updated = ts if updated is None else max(updated, ts)
    return {
        "date": date_str,
        "xlsx_path": f"/{xlsx.relative_to(EXPORTS_ROOT)}",
        "csv_path": f"/{csv.relative_to(EXPORTS_ROOT)}",
        "xlsx_exists": xlsx.exists(),
        "csv_exists": csv.exists(),
        "exists": xlsx.exists() or csv.exists(),
        "updated_at": updated,
    }


def _category_csv_path(category: str, date_str: str) -> Path:
    return EXPORTS_ROOT / date_str[:4] / date_str[5:7] / category / f"{DAILY_STEM}_{category}_{date_str}.csv"


def _recent_7d() -> list[dict]:
    """最近 7 个业务日期的每日正式数据文件（首页「最近 7 天价格数据」）。

    业务日期取自每日正式文件名（SMM锂电现货价格_{date}.xlsx/csv），
    绝不使用文件 mtime 判定归属；只含正式每日文件，排除 JSON/临时快照/中间产物。
    """
    out = []
    for d in _daily_dates_from_exports()[:7]:
        files = _daily_files(d)
        if not files["exists"]:
            continue
        xlsx = EXPORTS_ROOT / files["xlsx_path"].lstrip("/")
        out.append({
            "date": d,
            "name": f"{DAILY_STEM}_{d}.xlsx" if files["xlsx_exists"] else f"{DAILY_STEM}_{d}.csv",
            "path": files["xlsx_path"] if files["xlsx_exists"] else files["csv_path"],
            "xlsx_exists": files["xlsx_exists"],
            "csv_path": files["csv_path"],
            "csv_exists": files["csv_exists"],
            "size": xlsx.stat().st_size if xlsx.exists() else None,
            "modified": files["updated_at"],  # 仅作「文件更新时间」展示
            "status": _completeness_for(d).get("status"),
        })
    return out


def _load_manifest(date_str: str) -> dict | None:
    p = EXPORTS_ROOT / date_str[:4] / date_str[5:7] / "每日汇总" / f"SMM数据状态_{date_str}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _all_manifests() -> list[tuple[str, dict]]:
    """(date, manifest) 列表，按日期降序。"""
    out = []
    try:
        for p in EXPORTS_ROOT.glob("*/*/每日汇总/SMM数据状态_*.json"):
            m = re.search(r"SMM数据状态_(\d{4}-\d{2}-\d{2})\.json$", p.name)
            if not m:
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            out.append((m.group(1), data))
    except OSError:
        pass
    return sorted(out, key=lambda x: x[0], reverse=True)


def _completeness_for(date_str: str) -> dict:
    """某日期的采集完整性（优先 manifest，其次分类文件计数）。"""
    man = _load_manifest(date_str)
    if man:
        c = man.get("collection", {})
        exp, suc, fail = (c.get("categories_expected", 0),
                          c.get("categories_succeeded", 0),
                          c.get("categories_failed", 0))
        if fail == 0 and suc >= exp > 0:
            status = "完整"
        elif suc > 0:
            status = "部分成功"
        else:
            status = "异常"
        return {"expected": exp, "success": suc, "failed": fail,
                "missing_categories": c.get("missing_categories", []),
                "status": status, "source": "manifest"}
    # 无 manifest：用分类文件存在数 vs canonical
    canonical = _portal_config().get("canonical_categories") or []
    ok = [c for c in canonical if _category_csv_path(c, date_str).exists()]
    return {"expected": len(canonical), "success": len(ok), "failed": len(canonical) - len(ok),
            "missing_categories": sorted(set(canonical) - set(ok)),
            "status": "完整" if len(ok) == len(canonical) and canonical else ("部分成功" if ok else "异常"),
            "source": "files"}


def _compute_gaps() -> list[dict]:
    """数据缺口：DB 有行但无每日导出文件的日期（2026-07 起）。"""
    gaps = []
    rows = _db_query(
        "SELECT price_date, COUNT(*) AS c FROM lithium_spot_prices "
        "WHERE price_date >= '2026-07-01' "
        "AND price_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' "
        "GROUP BY price_date ORDER BY price_date")
    for r in rows:
        d = r["price_date"]
        if not d:
            continue
        if not (EXPORTS_ROOT / d[:4] / d[5:7] / "每日汇总" / "CSV" / f"{DAILY_STEM}_{d}.csv").exists():
            gaps.append({"date": d, "reason": f"数据库有 {r['c']} 行但无每日导出文件"})
    return gaps


def _trend_series(product=None, category=None, date_from=None, date_to=None) -> list[dict]:
    """产品/分类趋势序列：每 (product, spec) 每个 price_date 取最新 collected_at 的合法行。"""
    sql = ("SELECT * FROM lithium_spot_prices WHERE validation_status != 'invalid'")
    params: list = []
    if product:
        sql += " AND product_name = ?"
        params.append(product)
    if category:
        sql += " AND category = ?"
        params.append(category)
    if date_from:
        sql += " AND price_date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND price_date <= ?"
        params.append(date_to)
    sql += " ORDER BY price_date, collected_at DESC"
    rows = _db_query(sql, tuple(params))

    grouped: dict = {}
    for r in rows:
        key = (r["product_name"], r.get("specification") or "")
        grouped.setdefault(key, []).append(r)

    out = []
    for (prod, spec), rs in grouped.items():
        pts, seen = [], set()
        for r in rs:
            d = r["price_date"]
            if d in seen:
                continue
            seen.add(d)
            pts.append({"price_date": d,
                        "average_price": _num(r.get("average_price")),
                        "min_price": _num(r.get("min_price")),
                        "max_price": _num(r.get("max_price")),
                        "change_value": _num(r.get("change_value")),
                        "validation_status": r.get("validation_status"),
                        "collected_at": _fmt_ts(r.get("collected_at"))})
        if pts:
            out.append({"product": prod, "specification": spec,
                        "unit": rs[0].get("unit") or "", "category": rs[0].get("category") or "",
                        "points": pts})
    return out


def _is_stale(latest: str, global_latest: str | None, max_days: int = 14) -> bool:
    """该产品最新数据日期落后全局最新日期超过 max_days 天 → 数据较旧（仅标记，不隐藏）。"""
    try:
        if not global_latest:
            return False
        return (datetime.strptime(global_latest, "%Y-%m-%d")
                - datetime.strptime(latest, "%Y-%m-%d")).days > max_days
    except ValueError:
        return False


def _metrics(latest_db_date: str | None) -> list[dict]:
    """首页关键指标卡数据（值/较上一有效数据日期变化/近7日 spark/更新时间）。

    取值规则：以该 product（product 留空时取整个 category）最近一个
    price_date 为准；同一日期存在多个规格序列时取平均值——避免页面
    结构调整后卡片停留在某个已断更的旧序列上。
    涨跌幅 = (最新有效价 - 上一有效数据日期价格) / 上一有效价 × 100，
    全部来自 DB 真实记录；spark_points 逐点带日期，可直接追溯。
    """
    cfg = _portal_config()
    out = []
    for m in cfg.get("metrics") or []:
        series_list = _trend_series(m.get("product") or None, m.get("category"))
        series_list = [s for s in series_list if s["points"]]
        if not series_list:
            continue
        # 按日期聚合所有序列：{date: [(avg, collected_at), ...]}
        by_date: dict = {}
        for s in series_list:
            for p in s["points"]:
                if p["average_price"] is None:
                    continue
                by_date.setdefault(p["price_date"], []).append(
                    (p["average_price"], p["collected_at"]))
        dates = sorted(by_date)
        if not dates:
            continue
        latest = dates[-1]
        prev_date = dates[-2] if len(dates) >= 2 else None

        def _day_avg(day: str) -> float:
            vs = [v for v, _ in by_date[day]]
            return sum(vs) / len(vs)

        value = round(_day_avg(latest), 4)
        prev = _day_avg(prev_date) if prev_date is not None else None
        change = round(value - prev, 4) if prev is not None else None
        change_pct = round(change / prev * 100, 2) if change is not None and prev else None
        updated = max((ts for _, ts in by_date[latest] if ts), default=None)
        unit = next((s["unit"] for s in series_list
                     if s["unit"] and s["points"][-1]["price_date"] == latest), "")
        out.append({
            "key": m.get("key"), "name": m.get("name"),
            "category": m.get("category"), "product": m.get("product"),
            "group": m.get("group"),
            "unit": unit,
            "value": value, "change": change, "change_pct": change_pct,
            # sparkline 为兼容保留；spark_points 带日期，逐点可追溯到 DB 真实记录
            "sparkline": [round(_day_avg(d), 4) for d in dates[-7:]],
            "spark_points": [{"date": d, "value": round(_day_avg(d), 4)}
                             for d in dates[-7:]],
            "price_date": latest,
            "prev_date": prev_date,
            "is_stale": _is_stale(latest, latest_db_date),
            "updated_at": updated,
        })
    return out


# ── 首页「重点产品价格」模块 ─────────────────────────────
# 数据真实性要求：全部价格/日期/涨跌只来自 lithium_spot_prices 真实记录；
# 产品身份 = (category, product_name, specification) 精确三元组（绝不用模糊名称匹配）；
# 7d/30d 窗口只含 DB 真实存在的日期，周末/节假日/采集缺口一律不补点。

def _key_product_defs() -> list[dict]:
    """config key_products 段：有序展示产品定义（key 唯一、db 至少 1 个三元组）。

    非法条目（缺 key、db 为空、key 重复）跳过并忽略；specification 缺失按空串精确匹配。
    """
    cfg = _portal_config()
    defs: list[dict] = []
    seen: set[str] = set()
    for e in cfg.get("key_products") or []:
        key = str(e.get("key") or "")
        db = e.get("db") or []
        if not key or key in seen or not db:
            continue
        seen.add(key)
        defs.append({
            "key": key,
            "display_name": str(e.get("display_name") or key),
            "full_name": str(e.get("full_name") or e.get("display_name") or key),
            "business_category": str(e.get("business_category") or "未分类"),
            "subcategory": str(e.get("subcategory") or ""),
            "order": int(e.get("order") or 0) if str(e.get("order") or "").lstrip("-").isdigit() else 0,
            "db": [{"category": str(t.get("category") or ""),
                    "product_name": str(t.get("product_name") or ""),
                    "specification": str(t.get("specification") or "")} for t in db
                   if t.get("category") and t.get("product_name")],
        })
    return defs


def _key_product_rows() -> tuple[list[dict], dict]:
    """一次查询取回全部 key_products 三元组的合法行。

    返回 (defs, by_triple)：by_triple[(category, product_name, specification)] 为按
    price_date 升序的行列表；同一 (三元, 日期) 只留 collected_at 最新（同刻取 id 大者，
    与 verify_data_consistency._DAY_AVG_SQL 口径一致）。DB 不可用或配置为空 → ([], {})。
    """
    defs = _key_product_defs()
    if not defs:
        return [], {}
    conds, params = [], []
    triples = []
    for e in defs:
        for t in e["db"]:
            conds.append("(category = ? AND product_name = ? AND specification = ?)")
            params.extend([t["category"], t["product_name"], t["specification"]])
            triples.append((t["category"], t["product_name"], t["specification"]))
    sql = ("SELECT category, product_name, specification, unit, price_date, "
           "average_price, min_price, max_price, collected_at "
           "FROM lithium_spot_prices WHERE validation_status != 'invalid' "
           f"AND ({' OR '.join(conds)}) "
           "ORDER BY price_date, collected_at DESC, id DESC")
    rows = _db_query(sql, tuple(params))
    by_triple: dict = {}
    for r in rows:
        key = (r["category"], r["product_name"], r["specification"])
        seq = by_triple.setdefault(key, [])
        if seq and seq[-1]["price_date"] == r["price_date"]:
            continue  # 同一日期只留 collected_at 最新行（已按降序排序）
        seq.append(r)
    for seq in by_triple.values():
        seq.reverse()  # 升序
    return defs, by_triple


def _key_product_series(e: dict, by_triple: dict) -> list[dict]:
    """一个展示产品 → 一条按 price_date 升序的合并序列。

    1..n 个 DB 三元组合并（规格改名对在改名日边界天然连续）；average_price 为 NULL
    的点跳过（绝不填充）。每点保留真实 unit/collected_at 供卡片与校验追溯。
    """
    pts: list[dict] = []
    for t in e["db"]:
        for r in by_triple.get((t["category"], t["product_name"], t["specification"]), []):
            v = _num(r.get("average_price"))
            if v is None:
                continue
            pts.append({"price_date": str(r["price_date"]),
                        "value": v,
                        "unit": str(r.get("unit") or ""),
                        "collected_at": _fmt_ts(r.get("collected_at"))})
    pts.sort(key=lambda p: p["price_date"])
    return pts


def _key_product_card(e: dict, pts: list[dict], db_latest: str | None) -> dict:
    """单卡数据（口径与 _metrics 一致）。

    - value = 最新真实日期的 average_price；prev = 同一合并序列上一条真实记录（跨改名对）
    - change_pct = (value - prev) / prev × 100；无 prev 或 prev=0 → None
    - spark_points = 最新数据日期往前 7 个自然日窗口内的真实点 [{date, value}]
      （逐点可追溯到 DB；周末/节假日/采集缺口天然缺席，绝不补点）
    - 数据日期(price_date) 与 更新时间(collected_at) 分开；is_stale 沿用全局口径
    - 无任何数据 → value/change/change_pct/price_date/updated_at 全 None
    """
    card = {
        "key": e["key"], "display_name": e["display_name"], "full_name": e["full_name"],
        "business_category": e["business_category"], "subcategory": e["subcategory"],
        "value": None, "unit": "", "change": None, "change_pct": None,
        "prev_date": None, "price_date": None, "updated_at": None, "is_stale": False,
        "spark_points": [], "has_history": len(pts) >= 2,
    }
    if not pts:
        return card
    latest = pts[-1]
    prev = pts[-2] if len(pts) >= 2 else None
    card["value"] = round(latest["value"], 4)
    card["unit"] = latest["unit"] or (prev["unit"] if prev else "")
    card["price_date"] = latest["price_date"]
    card["updated_at"] = latest["collected_at"]
    card["is_stale"] = _is_stale(latest["price_date"], db_latest)
    # 一周趋势 = 最新数据日期往前 7 个自然日窗口内的真实点（非最近 7 个记录）
    spark_from = None
    try:
        from datetime import timedelta
        spark_from = (datetime.strptime(latest["price_date"], "%Y-%m-%d")
                      - timedelta(days=6)).strftime("%Y-%m-%d")
    except ValueError:
        spark_from = None
    card["spark_points"] = [{"date": p["price_date"], "value": round(p["value"], 4)}
                            for p in pts if spark_from is None or p["price_date"] >= spark_from]
    if prev is not None:
        card["prev_date"] = prev["price_date"]
        card["change"] = round(card["value"] - prev["value"], 4)
        if prev["value"]:
            card["change_pct"] = round(card["change"] / prev["value"] * 100, 2)
    return card


def _key_products_payload() -> dict:
    """GET /api/key-products 响应体（模块级纯函数，verify 脚本可直接调用）。

    按配置顺序组装 business_category → subcategory → products 分组，另附扁平
    products 列表供前端搜索。全部 20 卡一次返回（首页不做每产品独立请求）。
    """
    defs, by_triple = _key_product_rows()
    db_latest = _db_latest_date()
    products = [_key_product_card(e, _key_product_series(e, by_triple), db_latest) for e in defs]
    by_key = {p["key"]: p for p in products}
    groups: list[dict] = []
    for e in defs:
        p = by_key.get(e["key"])
        if p is None:
            continue
        g = next((x for x in groups if x["key"] == e["business_category"]), None)
        if g is None:
            g = {"key": e["business_category"], "name": e["business_category"], "subcategories": []}
            groups.append(g)
        sub = next((x for x in g["subcategories"] if x["key"] == e["subcategory"]), None)
        if sub is None:
            sub = {"key": e["subcategory"], "name": e["subcategory"], "products": []}
            g["subcategories"].append(sub)
        sub["products"].append(p)
    return {
        "meta": {"generated_at": datetime.now().isoformat(timespec="seconds"), "source": "sqlite"},
        "groups": groups,
        "products": products,
    }


def _key_product_history_payload(key: str, range_str: str = "30d") -> dict:
    """GET /api/key-products/history 响应体（按需加载，不进首页 payload）。

    range 仅接受 7d|30d（默认 30d）；窗口 = 该产品最新真实价格日期往前 6/29 天，
    只返回窗口内 DB 真实存在的日期（缺口/周末天然缺席，绝不补点）。
    """
    if range_str not in ("7d", "30d"):
        range_str = "30d"
    days = 7 if range_str == "7d" else 30
    defs, by_triple = _key_product_rows()
    e = next((x for x in defs if x["key"] == key), None)
    if e is None:
        return {"error": "未知产品 key", "data": []}
    pts = _key_product_series(e, by_triple)
    latest_date = pts[-1]["price_date"] if pts else None
    date_from = None
    if latest_date:
        try:
            from datetime import timedelta
            date_from = (datetime.strptime(latest_date, "%Y-%m-%d")
                         - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        except ValueError:
            date_from = None
    data = [{"date": p["price_date"], "price": round(p["value"], 4)}
            for p in pts if date_from is None or p["price_date"] >= date_from]
    unit = pts[-1]["unit"] if pts else ""
    return {
        "key": key, "display_name": e["display_name"], "unit": unit,
        "range": range_str, "latest_date": latest_date, "date_from": date_from,
        "data": data,
    }


def _rankings(limit: int = 5, as_of: str | None = None) -> dict:
    """涨跌榜：as_of 基准日之前 DB 最近两个有数据日期的相邻价格变化。

    规则（数据真实性要求）：
    - 涨幅榜只含 pct > 0（降序）；跌幅榜只含 pct < 0（升序）；pct == 0 两榜都不进
    - 不足 N 项就返回几项，绝不为了凑满把上涨/持平塞进跌幅榜
    - 两榜天然互斥（pct>0 与 pct<0 不相交），同一产品不会同时上榜
    - 次日采集模式：当天数据视为尚未发布，窗口取 as_of（默认服务器当天）
      之前的最近两个有效 price_date，通过 based_on 标注供页面展示对比日期
    """
    if as_of is None:
        as_of = datetime.now().strftime("%Y-%m-%d")
    window = _db_query(
        "SELECT DISTINCT price_date FROM lithium_spot_prices "
        "WHERE price_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' "
        "AND price_date < ? "
        "ORDER BY price_date DESC LIMIT 2", (as_of,))
    window_dates = [str(r["price_date"]) for r in window]
    rows = _db_query(
        "SELECT * FROM lithium_spot_prices WHERE validation_status != 'invalid' "
        "AND price_date IN (SELECT DISTINCT price_date FROM lithium_spot_prices "
        "WHERE price_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' "
        "AND price_date < ? "
        "ORDER BY price_date DESC LIMIT 2) "
        "ORDER BY price_date, collected_at DESC, id DESC", (as_of,))
    by_key: dict = {}
    for r in rows:
        key = (r["category"], r["product_name"], r.get("specification") or "", r.get("unit") or "")
        avg = _num(r.get("average_price"))
        if avg is None:
            continue
        by_key.setdefault(key, {})[str(r["price_date"])] = avg
    changes = []
    for (cat, prod, spec, unit), dates in by_key.items():
        ds = sorted(dates)
        if len(ds) < 2:
            continue
        prev_v, last_v = dates[ds[-2]], dates[ds[-1]]
        if not prev_v:
            continue
        change = round(last_v - prev_v, 4)
        pct = round(change / prev_v * 100, 2)
        changes.append({"product": prod, "category": cat, "specification": spec,
                        "unit": unit, "value": last_v, "change": change,
                        "pct": pct, "prev_date": ds[-2], "date": ds[-1]})
    gainers = sorted((c for c in changes if c["pct"] > 0),
                     key=lambda x: x["pct"], reverse=True)[:limit]
    losers = sorted((c for c in changes if c["pct"] < 0),
                    key=lambda x: x["pct"])[:limit]
    return {
        "top_gainers": gainers,
        "top_losers": losers,
        "based_on": {
            "date": window_dates[0] if window_dates else None,
            "prev_date": window_dates[1] if len(window_dates) >= 2 else None,
        },
    }


# ── HTTP Handler ─────────────────────────────────────────

class DataCenterHandler(http.server.SimpleHTTPRequestHandler):
    """SMM 数据门户请求处理器。"""

    def __init__(self, *args, **kwargs):
        # 文件下载的根目录
        super().__init__(*args, directory=str(EXPORTS_ROOT), **kwargs)

    def send_response(self, code, message=None):
        """统一安全响应头（对所有响应生效）。"""
        super().send_response(code, message)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "same-origin")

    # ── 会话鉴权（替代 HTTP Basic） ────────────────────

    @staticmethod
    def _safe_next(raw_next: str | None) -> str | None:
        """next 参数校验：仅允许站内页面路径，防开放重定向。"""
        if not raw_next:
            return None
        if not raw_next.startswith("/") or raw_next.startswith("//") or raw_next.startswith("/api/"):
            return None
        return raw_next

    @staticmethod
    def _is_public(path: str) -> bool:
        return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)

    def _wants_json(self) -> bool:
        """API 请求或显式接受 JSON → 返回 JSON 错误体；否则 HTML 跳转/页面。"""
        path = self.path.split("?")[0]
        return path.startswith("/api/") or "application/json" in (self.headers.get("Accept") or "")

    def _cookie_token(self) -> str | None:
        header = self.headers.get("Cookie", "")
        for part in header.split(";"):
            k, _, v = part.strip().partition("=")
            if k == SESSION_COOKIE and v:
                return v
        return None

    def _unauthenticated(self):
        """未登录：HTML → 302 登录页；API → 401 JSON。"""
        if self._wants_json():
            self._serve_json({"error": "unauthorized"}, status=401)
        else:
            nxt = self._safe_next(self.path)
            loc = "/login" + (f"?next={quote(nxt)}" if nxt else "")
            self.send_response(302)
            self.send_header("Location", loc)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def _forbidden(self, message: str = "无权限访问", code: str | None = None):
        """无权限：API → 403 JSON；强制改密 → 302 改密页；其余 → 403 页面。"""
        if self._wants_json():
            payload = {"error": message}
            if code:
                payload["code"] = code
            self._serve_json(payload, status=403)
        elif code == "PASSWORD_CHANGE_REQUIRED":
            self.send_response(302)
            self.send_header("Location", "/account")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._serve_403_page()

    def _serve_503(self):
        if self._wants_json():
            self._serve_json({"error": "认证服务暂时不可用"}, status=503)
        else:
            body = "<h1>503</h1><p>认证服务暂时不可用，请稍后重试</p>".encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def _session_gate(self) -> bool:
        """会话闸门：公开路径放行；其余校验 Session → 强制改密 → 管理员角色。

        返回 False 表示已写出响应、请求终止。auth.db 故障 → 503，绝不故障开放。
        """
        path = unquote(self.path.split("?")[0])
        self._user = None
        self._session = None
        self._session_token = None
        if self._is_public(path):
            return True

        token = self._cookie_token()
        if not token:
            self._unauthenticated()
            return False
        try:
            sess = AUTH_STORE.lookup_session(token)
        except sqlite3.Error:
            self._serve_503()
            return False
        if sess is None:
            self._unauthenticated()
            return False
        self._user = sess["user"]
        self._session = sess
        self._session_token = token

        # 初始密码未改：除改密/登出/自身信息/CSRF 外全部拦截
        if sess["user"]["must_change_password"]:
            allowed = {"/account", "/api/auth/me", "/api/auth/change-password",
                       "/api/auth/logout", "/api/auth/csrf"}
            if path not in allowed:
                self._forbidden(code="PASSWORD_CHANGE_REQUIRED", message="请先修改初始密码")
                return False

        # 管理员路径：服务端角色校验（前端隐藏按钮仅为体验优化）
        # 含 /admin*、/api/admin/* 以及数据质量页（仅管理员可见）
        is_admin_path = (path == "/admin" or path.startswith("/admin/")
                         or path.startswith("/api/admin/")
                         or path in ("/quality", "/api/quality"))
        if is_admin_path:
            if sess["user"]["role"] != "admin":
                self._forbidden(message="无管理员权限")
                return False
            if path in ("/admin", "/admin/users"):
                try:
                    AUTH_STORE.audit("ADMIN_PAGE_VIEW", sess["user"]["username"],
                                     self.client_address[0] if self.client_address else "")
                except sqlite3.Error:
                    pass

        try:
            AUTH_STORE.touch_session(token)
        except sqlite3.Error:
            pass  # 续期失败不影响本次请求
        return True

    # ── POST（登录/登出/改密） ─────────────────────────

    MAX_BODY = 16 * 1024

    def _read_json_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > self.MAX_BODY:
            return None
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _check_csrf(self) -> bool:
        header = self.headers.get("X-CSRF", "")
        if not header or not self._session_token:
            return False
        try:
            return AUTH_STORE.check_csrf(self._session_token, header)
        except sqlite3.Error:
            return False

    def _session_cookie_str(self, token: str, ttl_hours: float) -> str:
        cfg = _portal_config().get("auth") or {}
        secure = "; Secure" if cfg.get("cookie_secure") else ""
        return (f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/"
                f"; Max-Age={int(ttl_hours * 3600)}{secure}")

    def _clear_cookie_str(self) -> str:
        return f"{SESSION_COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"

    def _register_allowed(self, ip: str) -> bool:
        """注册节流：同 IP 每小时最多成功注册 REGISTER_MAX_PER_HOUR 次。"""
        now = time.time()
        with _register_lock:
            hits = _register_attempts.setdefault(ip, [])
            _register_attempts[ip] = [t for t in hits if now - t < 3600]  # 顺带清理过期
            return len(_register_attempts[ip]) < REGISTER_MAX_PER_HOUR

    def _register_mark(self, ip: str) -> None:
        with _register_lock:
            _register_attempts.setdefault(ip, []).append(time.time())

    def _handle_register(self):
        """POST /api/auth/register（公开，CSRF 豁免——注册前尚无会话）。

        安全要点：只读 username/password 两个字段，role 等其余字段一律忽略，
        服务端强制 role='user'，前端无法注册管理员账号。
        """
        ip = self.client_address[0] if self.client_address else "?"
        if not self._register_allowed(ip):
            self._serve_json({"error": "注册过于频繁，请稍后再试"}, status=429)
            return
        body = self._read_json_body()
        if not body:
            self._serve_json({"error": "参数不完整"}, status=400)
            return
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        try:
            status, msg = AUTH_STORE.register_user(username, password)
        except sqlite3.Error:
            self._serve_503()
            return
        if status == "duplicate":
            self._serve_json({"error": msg}, status=409)
            return
        if status != "ok":
            self._serve_json({"error": msg}, status=400)
            return
        self._register_mark(ip)  # 仅成功注册计数
        ua = self.headers.get("User-Agent", "")
        AUTH_STORE.audit("REGISTER", username, ip, ua)
        AUTH_STORE.write_event("REGISTER", "info", f"新用户注册成功: {username}")
        self._serve_json({"ok": True, "message": "注册成功，请登录"}, status=201)

    def _handle_login(self):
        """POST /api/auth/login（公开，CSRF 豁免——登录前尚无会话）。"""
        body = self._read_json_body()
        if not body or not body.get("username") or not body.get("password"):
            self._serve_json({"error": "用户名或密码错误"}, status=401)
            return
        ip = self.client_address[0] if self.client_address else "?"
        ua = self.headers.get("User-Agent", "")
        try:
            status, user, retry = AUTH_STORE.authenticate(body["username"], body["password"], ip, ua)
        except sqlite3.Error:
            self._serve_503()
            return
        if status == "locked":
            # 诊断日志：只记用户名和 IP，绝不记密码
            print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - "
                  f"鉴权: 锁定拒绝 ip={ip} user={body['username']!r} 剩余{retry}s", flush=True)
            self._serve_json({"error": "尝试次数过多，请稍后再试", "retry_after": retry}, status=429)
            return
        if status == "disabled":
            # 密码正确但账号被停用/注销（authenticate 仅在凭据有效时返回，防枚举）
            self._serve_json({"error": "账号已停用"}, status=401)
            return
        if status != "ok" or user is None:
            print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - "
                  f"鉴权: 失败 ip={ip} user={body['username']!r}", flush=True)
            self._serve_json({"error": "用户名或密码错误"}, status=401)
            return
        token, _csrf = AUTH_STORE.create_session(user["id"], ip, ua)
        ttl = (AUTH_STORE.admin_session_ttl_hours if user["role"] == "admin"
               else AUTH_STORE.session_ttl_hours)
        self._serve_json({"ok": True, "username": user["username"], "role": user["role"],
                          "must_change_password": bool(user["must_change_password"]),
                          "next": self._safe_next(body.get("next"))},
                         extra_headers=[("Set-Cookie", self._session_cookie_str(token, ttl))])

    def _handle_logout(self):
        if not self._check_csrf():
            self._serve_json({"error": "CSRF 校验失败"}, status=403)
            return
        if self._session_token and self._user:
            try:
                AUTH_STORE.audit("LOGOUT", self._user["username"],
                                 self.client_address[0] if self.client_address else "")
                AUTH_STORE.delete_session(self._session_token)
            except sqlite3.Error:
                pass
        self._serve_json({"ok": True}, extra_headers=[("Set-Cookie", self._clear_cookie_str())])

    def _handle_change_password(self):
        if not self._check_csrf():
            self._serve_json({"error": "CSRF 校验失败"}, status=403)
            return
        body = self._read_json_body()
        if not body or not body.get("current") or not body.get("new"):
            self._serve_json({"error": "参数不完整"}, status=400)
            return
        try:
            ok, msg = AUTH_STORE.change_password(
                self._user["id"], body["current"], body["new"],
                self.client_address[0] if self.client_address else "",
                self.headers.get("User-Agent", ""))
        except sqlite3.Error:
            self._serve_503()
            return
        if not ok:
            self._serve_json({"error": msg}, status=400)
            return
        # 改密后旧会话全部失效，强制重新登录
        self._serve_json({"ok": True, "message": msg},
                         extra_headers=[("Set-Cookie", self._clear_cookie_str())])

    # ── 管理员：用户管理 ───────────────────────────────

    def _handle_admin_users_get(self):
        """GET /api/admin/users（闸门已做 role==admin 校验）。"""
        try:
            users = AUTH_STORE.list_users()
        except sqlite3.Error:
            self._serve_503()
            return
        self._serve_json({"users": users})

    def _handle_admin_users_post(self):
        """POST /api/admin/users：{id, action∈disable|enable|reset_password|delete}。

        防护：CSRF 必检；目标必须存在且 role=='user'（不能操作管理员账号）；
        不能对自己操作。所有动作写审计与运维事件。
        """
        if not self._check_csrf():
            self._serve_json({"error": "CSRF 校验失败"}, status=403)
            return
        body = self._read_json_body()
        if not body or not isinstance(body.get("id"), int) or body.get("action") not in (
                "disable", "enable", "reset_password", "delete"):
            self._serve_json({"error": "参数不完整"}, status=400)
            return
        user_id, action = body["id"], body["action"]
        try:
            target = AUTH_STORE.get_user_by_id(user_id)
        except sqlite3.Error:
            self._serve_503()
            return
        if target is None:
            self._serve_json({"error": "目标用户不存在"}, status=400)
            return
        if target["role"] != "user":
            self._serve_json({"error": "不能对管理员账号执行此操作"}, status=400)
            return
        if self._user and user_id == self._user["id"]:
            self._serve_json({"error": "不能对自己执行此操作"}, status=400)
            return
        admin_username = self._user["username"] if self._user else "?"
        ip = self.client_address[0] if self.client_address else ""
        ua = self.headers.get("User-Agent", "")
        detail = f"管理员操作目标用户: {target['username']}"
        try:
            if action == "disable":
                ok, msg = AUTH_STORE.set_user_active(user_id, False), f"已停用用户 {target['username']}"
                event = ("USER_DISABLED", "info", f"管理员 {admin_username} 停用用户 {target['username']}")
            elif action == "enable":
                ok, msg = AUTH_STORE.set_user_active(user_id, True), f"已启用用户 {target['username']}"
                event = ("USER_ENABLED", "info", f"管理员 {admin_username} 启用用户 {target['username']}")
            elif action == "reset_password":
                ok, msg = AUTH_STORE.reset_user_password(user_id), \
                    f"已重置用户 {target['username']} 的密码为 123456，该用户下次登录需修改密码"
                event = ("USER_PASSWORD_RESET", "info",
                         f"管理员 {admin_username} 重置用户 {target['username']} 的密码")
            else:  # delete
                ok, msg = AUTH_STORE.soft_delete_user(user_id), f"已删除用户 {target['username']}"
                event = ("USER_DELETED", "info",
                         f"管理员 {admin_username} 删除（注销）用户 {target['username']}")
        except sqlite3.Error:
            self._serve_503()
            return
        if not ok:
            self._serve_json({"error": "操作失败：目标用户不存在"}, status=400)
            return
        AUTH_STORE.audit("ADMIN_USER_" + action.upper(), admin_username, ip, ua, detail)
        AUTH_STORE.write_event(event[0], event[1], event[2])
        self._serve_json({"ok": True, "message": msg})

    def do_GET(self):
        """路由分发（安全闸 → 会话闸门 → 路由）。"""
        path = unquote(self.path.split("?")[0])

        # 第一道安全闸：任何路由分支之前拒绝路径穿越（含 %2e%2e 编码形式）
        if not _is_safe_raw(path):
            self.send_error(403, "Forbidden")
            return

        # 第二道闸门：会话鉴权（公开路径放行；其余必须登录）
        if not self._session_gate():
            return

        # 公开路径
        if path == "/health":
            self._serve_json({"status": "ok", "service": "smm-data-center",
                              "version": "1.0.0"})

        # 门户页面（/login 公开；/account /admin 由闸门校验）
        elif path in PAGE_MAP:
            self._serve_page(path)

        # 静态资源
        elif path.startswith("/static/"):
            self._serve_static(path)

        # 账号 API
        elif path == "/api/auth/me":
            u = self._user or {}
            self._serve_json({"username": u.get("username"), "role": u.get("role"),
                              "must_change_password": bool(u.get("must_change_password"))})
        elif path == "/api/auth/csrf":
            sess = self._session or {}
            self._serve_json({"csrf_token": sess.get("csrf_token", "")})

        # 管理员运维 API（闸门已做 role==admin 校验）
        elif path == "/api/admin/overview":
            self._serve_json(ops_monitor.aggregate_cached(
                _portal_config(), PROJECT_ROOT, DB_PATH, PORT, pid=os.getpid()))
        elif path == "/api/admin/tasks":
            self._serve_json({"meta": {"generated_at": datetime.now().isoformat(timespec="seconds")},
                              "tasks": ops_monitor.tasks(PROJECT_ROOT, _portal_config())})
        elif path == "/api/admin/data-quality":
            self._serve_json({"meta": {"generated_at": datetime.now().isoformat(timespec="seconds")},
                              "quality": ops_monitor.transform_validation(
                                  ops_monitor._latest_validation(PROJECT_ROOT))})
        elif path == "/api/admin/errors":
            self._serve_json({"errors": ops_monitor.errors_last_24h(PROJECT_ROOT)})
        elif path == "/api/admin/logs":
            q = parse_qs(urlsplit(self.path).query)
            try:
                day = int((q.get("day") or ["0"])[0])
                lines = int((q.get("lines") or ["200"])[0])
            except ValueError:
                day, lines = 0, 200
            self._serve_json(ops_monitor.read_log(
                PROJECT_ROOT, _portal_config(), (q.get("name") or ["cron"])[0], day, lines))
        elif path == "/api/admin/events":
            try:
                events = AUTH_STORE.list_events(50)
            except sqlite3.Error:
                events = []
            self._serve_json({"events": events})
        elif path == "/api/admin/audit":
            try:
                audit = AUTH_STORE.list_audit(50)
            except sqlite3.Error:
                audit = []
            self._serve_json({"audit": audit})
        elif path == "/api/admin/users":
            self._handle_admin_users_get()

        # 门户 API
        elif path == "/api/files":
            self._serve_api_files()
        elif path == "/api/stats":
            self._serve_api_stats()
        elif path == "/api/overview":
            self._serve_json(self._api_overview())
        elif path == "/api/latest":
            self._serve_json(self._api_latest(parse_qs(urlsplit(self.path).query)))
        elif path == "/api/history":
            self._serve_json(self._api_history(parse_qs(urlsplit(self.path).query)))
        elif path == "/api/categories":
            self._serve_json(self._api_categories())
        elif path == "/api/trends":
            self._serve_json(self._api_trends(parse_qs(urlsplit(self.path).query)))
        elif path == "/api/key-products":
            self._serve_json(self._api_key_products(parse_qs(urlsplit(self.path).query)))
        elif path == "/api/key-products/history":
            self._serve_json(self._api_key_product_history(parse_qs(urlsplit(self.path).query)))
        elif path == "/api/quality":
            self._serve_json(self._api_quality())
        elif path == "/api/topics":
            self._serve_json(self._api_topics())

        # ── 业务品种 API（每日报价 / 价格走势 / 月度报表 / 全量数据；登录闸门之内） ──
        elif path == "/api/portal/products":
            self._api_portal_products()
        elif path == "/api/portal/quotes":
            self._api_portal_quotes(parse_qs(urlsplit(self.path).query))
        elif path == "/api/portal/history":
            self._api_portal_history(parse_qs(urlsplit(self.path).query))
        elif path == "/api/portal/monthly":
            self._api_portal_monthly(parse_qs(urlsplit(self.path).query))
        elif path == "/api/portal/monthly/download":
            self._api_portal_monthly_download(parse_qs(urlsplit(self.path).query))
        elif path == "/api/portal/dataset/categories":
            self._api_portal_dataset_categories()
        elif path == "/api/portal/dataset/products":
            self._api_portal_dataset_products(parse_qs(urlsplit(self.path).query))
        elif path == "/api/portal/dataset/quotes":
            self._api_portal_dataset_quotes(parse_qs(urlsplit(self.path).query))
        elif path == "/api/portal/catalog/products":
            self._api_portal_catalog_products()
        elif path == "/api/portal/catalog/quotes":
            self._api_portal_catalog_quotes(parse_qs(urlsplit(self.path).query))
        elif path.startswith("/api/"):
            self._serve_json({"error": "not found"}, status=404)

        # 文件下载 / 目录浏览（需登录，位于会话闸门之内）
        else:
            self._serve_file(path)

    def do_POST(self):
        """写操作路由：登录公开；登出/改密需登录 + CSRF。"""
        path = unquote(self.path.split("?")[0])
        if not _is_safe_raw(path):
            self.send_error(403, "Forbidden")
            return

        if path == "/api/auth/login":
            self._handle_login()
            return
        if path == "/api/auth/register":
            self._handle_register()
            return

        if not self._session_gate():
            return

        if path == "/api/auth/logout":
            self._handle_logout()
        elif path == "/api/auth/change-password":
            self._handle_change_password()
        elif path == "/api/admin/users":
            self._handle_admin_users_post()
        else:
            self._serve_json({"error": "not found"}, status=404)

    # ── 基础响应 ──────────────────────────────────────

    def _serve_html_file(self, file_name: str, status: int = 200):
        """返回 static/ 下的 HTML 文件。"""
        file_path = _resolve_inside(STATIC_ROOT, file_name)
        if file_path is None or not file_path.is_file():
            self.send_error(404, "Not Found")
            return
        content = file_path.read_bytes()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def _serve_page(self, path: str):
        """返回门户页面（static/*.html）。"""
        self._serve_html_file(PAGE_MAP[path])

    def _serve_403_page(self):
        """无权限页面（static/403.html，自包含样式）。"""
        if (STATIC_ROOT / "403.html").is_file():
            self._serve_html_file("403.html", 403)
        else:
            self.send_error(403, "Forbidden")

    def _serve_static(self, path: str):
        """返回静态资源 (CSS / JS / HTML)。"""
        # 安全校验：穿越闸 + 黑名单 + 终局包含性校验（2026-08-13 漏洞修复）
        if not is_safe_path(path) or is_blocked(path):
            self.send_error(403, "Forbidden")
            return

        file_path = _resolve_inside(STATIC_ROOT, path.removeprefix("/static/"))
        if file_path is None:
            self.send_error(403, "Forbidden")
            return
        if not file_path.is_file():
            self.send_error(404, "Not Found")
            return

        # MIME 类型
        mime_map = {
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".html": "text/html; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".json": "application/json; charset=utf-8",
        }
        mime = mime_map.get(file_path.suffix, "application/octet-stream")

        content = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(content)

    def _serve_json(self, obj: dict, status: int = 200, extra_headers: list | None = None):
        """JSON 响应（认证端点 no-store，防止凭证类响应被缓存）。

        extra_headers 必须在 send_response 之后注入（如 Set-Cookie）。
        """
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        cache = "no-store" if self.path.startswith("/api/auth") else "no-cache"
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path: str):
        """文件下载或目录浏览（exports 根内）。"""
        if not is_safe_path(path):
            self.send_error(403, "Forbidden")
            return
        if is_blocked(path):
            self.send_error(403, "Forbidden")
            return

        # 纵深防御：解析后的绝对路径必须仍在 exports 根内（覆盖 stdlib 行为变化）
        resolved = _resolve_inside(EXPORTS_ROOT, path.lstrip("/"))
        if resolved is None:
            self.send_error(403, "Forbidden")
            return
        # 扩展名白名单：文件下载仅允许数据类扩展名（目录浏览不受限）
        if resolved.is_file() and resolved.suffix.lower() not in ALLOWED_EXTENSIONS:
            self.send_error(403, "Forbidden")
            return

        # 使用父类 SimpleHTTPRequestHandler 处理文件/目录
        super().do_GET()

    # ── 向后兼容 API ──────────────────────────────────

    def _serve_api_files(self):
        items = _scan_cached()
        for it in items:
            it.pop("mtime", None)
        self._serve_json(items)

    def _serve_api_stats(self):
        files = _scan_cached()
        file_count = sum(1 for f in files if f["type"] == "file")
        dir_count = sum(1 for f in files if f["type"] == "directory")
        latest = max((f.get("mtime", 0) for f in files), default=0)
        years = sorted({f["modified"][:4] for f in files if f.get("modified")})
        self._serve_json({
            "file_count": file_count,
            "directory_count": dir_count,
            "latest_update": datetime.fromtimestamp(latest).strftime("%Y-%m-%d %H:%M") if latest else "",
            "years": years,
        })

    # ── 门户 API ──────────────────────────────────────

    def _api_overview(self) -> dict:
        cfg = _portal_config()
        portal = cfg.get("portal") or {}
        latest = _latest_date()
        db_latest = _db_latest_date()

        # 今日必看
        today = {}
        if latest:
            files = _daily_files(latest)
            man = _load_manifest(latest)
            completeness = _completeness_for(latest)
            today = {
                "latest_date": latest,
                "db_price_date": db_latest,
                "data_source": "both" if latest == db_latest else ("exports" if files["exists"] else "sqlite"),
                "files": files,
                "completeness": completeness,
                "manifest_exists": man is not None,
            }

        # 固定汇总状态
        fixed = {"formal_file_path": "/固定汇总/SMM锂电现货价格_固定汇总.xlsx"}
        formal_file = EXPORTS_ROOT / "固定汇总" / "SMM锂电现货价格_固定汇总.xlsx"
        fixed["formal_exists"] = formal_file.exists()
        if formal_file.exists():
            fixed["formal_updated_at"] = datetime.fromtimestamp(
                formal_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        warnings: list[str] = []
        if latest and man:
            fs = man.get("fixed_summary") or {}
            fixed["formal_status"] = fs.get("formal_status")
            fixed["decision"] = fs.get("decision")
            fixed["temp_snapshot_path"] = fs.get("temp_snapshot_path")
            warnings.extend(fs.get("reasons") or [])
        elif latest:
            fixed["formal_status"] = _completeness_for(latest)["status"]
        gaps = _compute_gaps()
        if gaps:
            fixed["warnings"] = warnings + [f"{g['date']}: {g['reason']}" for g in gaps]

        # 最近 7 天价格数据（按业务日期，非文件 mtime）
        recent = _recent_7d()

        db_rows = _db_query("SELECT COUNT(DISTINCT price_date) AS dc, MIN(price_date) AS mn, MAX(price_date) AS mx FROM lithium_spot_prices")
        stats = {"date_count": db_rows[0]["dc"] if db_rows else 0,
                 "date_range": [db_rows[0]["mn"], db_rows[0]["mx"]] if db_rows and db_rows[0]["mn"] else []}

        return {
            "meta": {"generated_at": datetime.now().isoformat(timespec="seconds"), "source": "mixed"},
            "banner": {
                "title": portal.get("title", "锂电价格与回收业务数据中心"),
                "subtitle": portal.get("subtitle", ""),
            },
            "today_must_see": today,
            "metrics": _metrics(db_latest),
            "topics": [
                {"key": k, "name": t.get("name", k), "icon": t.get("icon", ""),
                 "url": f"/topics#{k}", "emphasis": bool(t.get("emphasis"))}
                for k, t in (cfg.get("topics") or {}).items()
            ],
            "recent_files": recent,
            "fixed_summary": fixed,
            "gaps": gaps,
            "stats": stats,
        }

    def _api_latest(self, q: dict) -> dict:
        date_str = (q.get("date") or [None])[0] or _latest_date()
        if not date_str:
            return {"meta": {"generated_at": datetime.now().isoformat(timespec="seconds"),
                             "source": "mixed"}, "error": "无可用数据日期"}
        gm = _group_map()
        files = _daily_files(date_str)
        completeness = _completeness_for(date_str)

        # 当日分类文件清单（按分组排序）
        categories = []
        groups = (_portal_config().get("groups") or {})
        for gk, gv in groups.items():
            for cat in gv.get("categories", []):
                p = _category_csv_path(cat, date_str)
                categories.append({
                    "category": cat, "group": gk,
                    "group_name": gv.get("name", ""), "group_icon": gv.get("icon", ""),
                    "file_path": f"/{p.relative_to(EXPORTS_ROOT)}",
                    "exists": p.exists(),
                })
        # 未映射分类兜底
        mapped = {c["category"] for c in categories}
        for cat in sorted(set(gm) - mapped):
            p = _category_csv_path(cat, date_str)
            categories.append({"category": cat, "group": "?", "group_name": "未分组",
                               "group_icon": "", "file_path": f"/{p.relative_to(EXPORTS_ROOT)}",
                               "exists": p.exists()})

        return {
            "meta": {"generated_at": datetime.now().isoformat(timespec="seconds"), "source": "mixed"},
            "date": date_str,
            "db_price_date": _db_latest_date(),
            "files": files,
            "categories": categories,
            "completeness": completeness,
            "metrics": _metrics(date_str),
            "rankings": _rankings(),
        }

    def _api_history(self, q: dict) -> dict:
        """正式历史数据档案检索。

        候选文件白名单（仅正式数据产品，近三日对比/JSON/临时文件/中间产物一律排除）：
          - 每日正式 Excel / CSV：SMM锂电现货价格_{date}.xlsx|csv
          - 规范日报：SMM锂电现货价格_规范日报_{date}.xlsx
          - 各分类每日 CSV：SMM锂电现货价格_{分类}_{date}.csv
        筛选条件（全部 AND 组合，日期一律取文件名业务日期，绝不用 mtime）：
          from/to（含边界）、group（A-F 业务分组）、category、q（文件名或数据内容反查）、分页
        """
        date_from = (q.get("from") or [None])[0]
        date_to = (q.get("to") or [None])[0]
        group = (q.get("group") or [None])[0]
        category = (q.get("category") or [None])[0]
        keyword = (q.get("q") or [None])[0]
        try:
            page = max(1, int((q.get("page") or ["1"])[0]))
        except ValueError:
            page = 1
        try:
            page_size = min(200, max(1, int((q.get("page_size") or ["50"])[0])))
        except ValueError:
            page_size = 50

        # 业务日期集合 = 每日正式文件的实际日期（自动识别，不写死）
        dates = _daily_dates_from_exports()
        date_min, date_max = (dates[-1], dates[0]) if dates else (None, None)

        def in_range(d: str) -> bool:
            if date_from and d < date_from:
                return False
            if date_to and d > date_to:
                return False
            return True

        gm = _group_map()
        group_cats = None
        if group:
            gcfg = (_portal_config().get("groups") or {}).get(group) or {}
            group_cats = set(gcfg.get("categories", []))

        # 关键词 → 数据内容反查（分类级文件名不含产品名，通过 SQLite 反查所属分类）
        q_cats = None
        if keyword:
            rows = _db_query(
                "SELECT DISTINCT category FROM lithium_spot_prices "
                "WHERE product_name LIKE ? OR category LIKE ?",
                (f"%{keyword}%", f"%{keyword}%"))
            q_cats = {str(r["category"]) for r in rows}

        # 构建候选文件（白名单模式）
        candidates: list[dict] = []
        cat_re = re.compile(rf"^{DAILY_STEM}_(.+?)_(\d{{4}}-\d{{2}}-\d{{2}})\.csv$")

        def add(p: Path, d: str, cat=None, kind="文件"):
            if not p.is_file():
                return
            stat = p.stat()
            g = gm.get(cat) or {}
            candidates.append({
                "name": p.name, "path": f"/{p.relative_to(EXPORTS_ROOT)}",
                "type": "file", "size": stat.st_size, "kind": kind, "date": d,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "category": cat, "group": g.get("key", ""), "group_name": g.get("name", ""),
            })

        for d in dates:
            if not in_range(d):
                continue
            f = _daily_files(d)
            if f["xlsx_exists"]:
                add(EXPORTS_ROOT / f["xlsx_path"].lstrip("/"), d, None, "每日总表")
            if f["csv_exists"]:
                add(EXPORTS_ROOT / f["csv_path"].lstrip("/"), d, None, "每日总表")
            rpt = EXPORTS_ROOT / d[:4] / d[5:7] / "每日汇总" / "Excel" / f"{DAILY_STEM}_规范日报_{d}.xlsx"
            add(rpt, d, None, "规范日报")

        for cat, ginfo in gm.items():
            if group_cats is not None and cat not in group_cats:
                continue
            if category and cat != category:
                continue
            if q_cats is not None and cat not in q_cats:
                continue
            for d in dates:
                if not in_range(d):
                    continue
                add(_category_csv_path(cat, d), d, cat, "分类数据")

        # 关键词：文件名匹配（对每日总表/规范日报），或数据内容反查命中的分类文件
        items = candidates
        if keyword:
            kw = keyword.lower()
            items = [i for i in items if kw in i["name"].lower()
                     or (i["category"] is not None and q_cats is not None and i["category"] in q_cats)]
        items.sort(key=lambda x: (x["date"] or "", x["path"]), reverse=True)
        total = len(items)
        start = (page - 1) * page_size
        items = items[start:start + page_size]

        history = EXPORTS_ROOT / "SMM锂电现货价格_历史汇总.xlsx"
        fixed = EXPORTS_ROOT / "固定汇总" / "SMM锂电现货价格_固定汇总.xlsx"

        def archive_info(p: Path, path_rel: str) -> dict:
            info = {"path": path_rel, "exists": p.is_file()}
            if p.is_file():
                st = p.stat()
                info["size"] = st.st_size
                info["modified"] = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            return info

        return {
            "meta": {"generated_at": datetime.now().isoformat(timespec="seconds"), "source": "exports"},
            "dates": dates,
            "date_min": date_min, "date_max": date_max,
            "total": total, "page": page, "page_size": page_size,
            "files": items,
            "history_summary": archive_info(history, "/SMM锂电现货价格_历史汇总.xlsx"),
            "fixed_summary": archive_info(fixed, "/固定汇总/SMM锂电现货价格_固定汇总.xlsx"),
        }

    def _api_categories(self) -> dict:
        cfg = _portal_config()
        gm = _group_map()
        latest_dates = _daily_dates_from_exports()[:3]
        groups = []
        flat = []
        for gk, gv in (cfg.get("groups") or {}).items():
            cats = []
            for cat in gv.get("categories", []):
                lf = None
                for d in latest_dates:
                    p = _category_csv_path(cat, d)
                    if p.exists():
                        lf = {"date": d, "path": f"/{p.relative_to(EXPORTS_ROOT)}", "exists": True}
                        break
                entry = {"category": cat, "group": gk,
                         "latest_file": lf or {"date": None, "path": None, "exists": False}}
                cats.append(entry)
                flat.append(entry)
            groups.append({"key": gk, "name": gv.get("name", ""),
                           "description": gv.get("description", ""), "icon": gv.get("icon", ""),
                           "categories": cats})
        return {
            "meta": {"generated_at": datetime.now().isoformat(timespec="seconds"), "source": "config"},
            "canonical_count": len(cfg.get("canonical_categories") or []),
            "groups": groups,
            "categories": flat,
        }

    def _api_trends(self, q: dict) -> dict:
        product = (q.get("product") or [None])[0]
        category = (q.get("category") or [None])[0]
        date_from = (q.get("from") or [None])[0]
        date_to = (q.get("to") or [None])[0]
        try:
            days = int((q.get("days") or ["30"])[0])
        except ValueError:
            days = 30
        if not date_from and days:
            # days 窗口：以最新 price_date 往前推
            latest = _db_latest_date()
            if latest:
                try:
                    from datetime import timedelta
                    date_from = (datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=days - 1)).strftime("%Y-%m-%d")
                except ValueError:
                    date_from = None

        series = _trend_series(product, category, date_from, date_to)
        if not series:
            return {"meta": {"generated_at": datetime.now().isoformat(timespec="seconds"),
                             "source": "sqlite"}, "error": "无趋势数据", "series": []}
        primary = series[0]
        pts = [p for p in primary["points"] if p["average_price"] is not None]
        change = None
        if len(pts) >= 2 and pts[-2]["average_price"]:
            change = round((pts[-1]["average_price"] - pts[-2]["average_price"]) / pts[-2]["average_price"] * 100, 2)
        return {
            "meta": {"generated_at": datetime.now().isoformat(timespec="seconds"), "source": "sqlite"},
            "product": product, "category": category,
            "date_from": date_from, "date_to": date_to,
            "series": series,
            "primary": {"product": primary["product"], "unit": primary["unit"],
                        "last_7d_change_pct": change, "valid_points": len(pts)},
        }

    def _api_key_products(self, q: dict) -> dict:
        """首页「重点产品价格」：全部产品卡（最新价/较上次/近7日真实点/双日期）一次返回。"""
        return _key_products_payload()

    def _api_key_product_history(self, q: dict) -> dict:
        """产品趋势详情：按需加载 7d/30d 窗口内 DB 真实价格点（缺口不补）。"""
        key = (q.get("key") or [""])[0]
        range_str = (q.get("range") or ["30d"])[0]
        return _key_product_history_payload(key, range_str)

    # ── 业务品种 API 实现（数据规则全部在 smm_collector.portal_service） ──

    def _portal_ready(self) -> bool:
        return portal_service is not None

    def _api_portal_products(self):
        """GET /api/portal/products：业务品种映射全量（49 行）+ 筛选维度 + 日期范围。"""
        products, meta = _business_products()
        if not self._portal_ready() or not products:
            self._serve_json({"error": "业务映射配置不可用", "meta": meta}, status=503)
            return
        con = _db_conn()
        try:
            self._serve_json(portal_service.product_meta_payload(products, meta, con))
        finally:
            con.close()

    def _api_portal_quotes(self, q: dict):
        """GET /api/portal/quotes：40 条 SMM 业务行最新报价（as_of 日期语义）。

        as_of 为空 = 最新可用报价；指定日期 = 该日（含）之前每品种最新报价，
        返回实际 price_date 与 collected_at，绝不出现所选日期之后的价格。
        """
        products, meta = _business_products()
        as_of = (q.get("as_of") or [None])[0]
        org = (q.get("org") or [None])[0]
        cat = (q.get("info_category") or [None])[0]
        kw = (q.get("q") or [None])[0]
        if as_of and portal_service.parse_date(as_of) is None:
            self._serve_json({"error": "as_of 格式非法（YYYY-MM-DD）"}, status=400)
            return
        con = _db_conn()
        try:
            payload = portal_service.quote_rows_payload(products, con, as_of=as_of, meta=meta)
        finally:
            con.close()
        if org or cat or kw:
            if kw:
                match_ids = {x["id"] for x in portal_service.search_products(
                    portal_service.smm_products(products), kw)}
            else:
                match_ids = None
            payload["rows"] = [
                r for r in payload["rows"]
                if (not org or r["organization"] == org)
                and (not cat or r["info_category"] == cat)
                and (match_ids is None or r["id"] in match_ids)]
        payload["meta"]["filters"] = {"org": org, "info_category": cat, "q": kw}
        self._serve_json(payload)

    def _api_portal_history(self, q: dict):
        """GET /api/portal/history?ids=a,b&from=&to=：统一日期窗口内的品种序列。"""
        products, meta = _business_products()
        ids = [i.strip() for i in (q.get("ids") or [""])[0].split(",") if i.strip()]
        date_from = (q.get("from") or [None])[0]
        date_to = (q.get("to") or [None])[0]
        con = _db_conn()
        try:
            self._serve_json(portal_service.history_payload(
                products, con, ids, date_from=date_from, date_to=date_to, meta=meta))
        finally:
            con.close()

    def _api_portal_monthly(self, q: dict):
        """GET /api/portal/monthly?month=YYYY-MM：月度业务报表预览（49 行）。"""
        products, meta = _business_products()
        month = (q.get("month") or [None])[0]
        if not month or portal_service.month_bounds(month) is None:
            self._serve_json({"error": "月份格式非法（应为 YYYY-MM）"}, status=400)
            return
        con = _db_conn()
        try:
            self._serve_json(portal_service.monthly_payload(products, con, month, meta))
        finally:
            con.close()

    def _api_portal_monthly_download(self, q: dict):
        """GET /api/portal/monthly/download?month=YYYY-MM：月度业务报表 xlsx 下载。

        内存生成（systemd ProtectSystem=strict 只读运行下也可用），不落盘、不覆盖任何历史文件。
        """
        products, meta = _business_products()
        month = (q.get("month") or [None])[0]
        if not month or portal_service.month_bounds(month) is None:
            self._serve_json({"error": "月份格式非法（应为 YYYY-MM）"}, status=400)
            return
        if monthly_report is None:
            self._serve_json({"error": "报表模块不可用"}, status=503)
            return
        try:
            con = _db_conn()
            try:
                buf = monthly_report.build_monthly_report_xlsx(products, con, month, meta)
            finally:
                con.close()
        except ValueError as e:
            self._serve_json({"error": str(e)}, status=400)
            return
        body = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type",
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition",
                         f"attachment; filename*=UTF-8''{quote(monthly_report.report_filename(month))}")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _api_portal_dataset_categories(self):
        """GET /api/portal/dataset/categories：全部采集分类（按 DB 实际数据动态生成）。"""
        con = _db_conn()
        try:
            cats = portal_service.dataset_categories(con)
        finally:
            con.close()
        self._serve_json({"meta": {"generated_at": datetime.now().isoformat(timespec="seconds"),
                                   "source": "sqlite"},
                          "categories": cats})

    def _api_portal_dataset_products(self, q: dict):
        """GET /api/portal/dataset/products?category=|categories=a,b：分类下产品清单（下拉）。"""
        category = (q.get("category") or [None])[0]
        categories = [c for c in (q.get("categories") or [""])[0].split(",") if c]
        con = _db_conn()
        try:
            prods = portal_service.dataset_products(con, category=category, categories=categories)
        finally:
            con.close()
        self._serve_json({"meta": {"generated_at": datetime.now().isoformat(timespec="seconds"),
                                   "source": "sqlite"},
                          "products": prods})

    def _api_portal_dataset_quotes(self, q: dict):
        """GET /api/portal/dataset/quotes：全量原始行分页（全部分类入口，不受 40 条业务限制）。

        categories=a,b 多分类并集（数据与报表分类多选）。
        """
        category = (q.get("category") or [None])[0]
        categories = [c for c in (q.get("categories") or [""])[0].split(",") if c]
        product = (q.get("product") or [None])[0]
        kw = (q.get("q") or [None])[0]
        date_from = (q.get("from") or [None])[0]
        date_to = (q.get("to") or [None])[0]
        try:
            page = max(1, int((q.get("page") or ["1"])[0]))
            page_size = min(200, max(1, int((q.get("page_size") or ["100"])[0])))
        except ValueError:
            page, page_size = 1, 100
        con = _db_conn()
        try:
            payload = portal_service.dataset_quotes(
                con, category=category, categories=categories, product=product, q=kw,
                date_from=date_from, date_to=date_to, page=page, page_size=page_size)
        finally:
            con.close()
        payload["meta"] = {"generated_at": datetime.now().isoformat(timespec="seconds"),
                           "source": "sqlite"}
        self._serve_json(payload)

    def _api_portal_catalog_products(self):
        """GET /api/portal/catalog/products：DB 全量采集产品目录（每日报价选择器用）。

        不受业务映射 40 条限制；身份 = 自然键去重，稳定 catalog id。
        """
        con = _db_conn()
        try:
            self._serve_json(portal_service.catalog_products(con))
        finally:
            con.close()

    def _api_portal_catalog_quotes(self, q: dict):
        """GET /api/portal/catalog/quotes?ids=...&as_of=YYYY-MM-DD：按 catalog id 列表返回最新报价。"""
        ids = [i.strip() for i in (q.get("ids") or [""])[0].split(",") if i.strip()]
        as_of = (q.get("as_of") or [None])[0]
        if as_of and portal_service.parse_date(as_of) is None:
            self._serve_json({"error": "as_of 格式非法（YYYY-MM-DD）"}, status=400)
            return
        con = _db_conn()
        try:
            self._serve_json(portal_service.catalog_quotes(con, ids, as_of=as_of))
        finally:
            con.close()

    def _api_quality(self) -> dict:
        manifests = _all_manifests()
        latest = manifests[0] if manifests else None

        today = {}
        if latest:
            d, man = latest
            c = man.get("collection") or {}
            fs = man.get("fixed_summary") or {}
            today = {
                "date": d,
                "data_date": man.get("data_date"),
                "collection_status": c.get("status"),
                "expected": c.get("categories_expected", 0),
                "success": c.get("categories_succeeded", 0),
                "failed": c.get("categories_failed", 0),
                "missing_categories": c.get("missing_categories", []),
                "valid": c.get("valid_count", 0),
                "warning": c.get("warning_count", 0),
                "invalid": c.get("invalid_count", 0),
                "date_alignment_ratio": c.get("date_alignment_ratio"),
                "fixed_summary_admitted": fs.get("eligible", False),
                "decision": fs.get("decision"),
                "formal_status": fs.get("formal_status"),
                "reasons": fs.get("reasons", []),
                "runs": man.get("runs", []),
            }
        else:
            latest_date = _latest_date()
            if latest_date:
                completeness = _completeness_for(latest_date)
                today = {"date": latest_date, "manifest_missing": True,
                         "expected": completeness["expected"], "success": completeness["success"],
                         "failed": completeness["failed"], "formal_status": completeness["status"],
                         "reasons": ["该日期尚无数据状态 manifest"]}

        history = []
        for d, man in manifests[:14]:
            c = man.get("collection") or {}
            fs = man.get("fixed_summary") or {}
            history.append({
                "date": d, "status": c.get("status"),
                "expected": c.get("categories_expected", 0),
                "success": c.get("categories_succeeded", 0),
                "failed": c.get("categories_failed", 0),
                "admitted": fs.get("eligible", False),
                "formal_status": fs.get("formal_status"),
            })

        formal_file = EXPORTS_ROOT / "固定汇总" / "SMM锂电现货价格_固定汇总.xlsx"
        fixed = {
            "formal_file_path": "/固定汇总/SMM锂电现货价格_固定汇总.xlsx",
            "formal_exists": formal_file.exists(),
            "formal_updated_at": (datetime.fromtimestamp(formal_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                                  if formal_file.exists() else None),
            "temp_snapshots": [],
        }
        snap_dir = EXPORTS_ROOT / "固定汇总" / "临时快照"
        if snap_dir.is_dir():
            for p in sorted(snap_dir.glob("*.xlsx"), reverse=True):
                fixed["temp_snapshots"].append({
                    "name": p.name, "path": f"/{p.relative_to(EXPORTS_ROOT)}",
                    "modified": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                })

        return {
            "meta": {"generated_at": datetime.now().isoformat(timespec="seconds"), "source": "manifest"},
            "today": today,
            "history": history,
            "fixed_summary": fixed,
            "gaps": _compute_gaps(),
        }

    def _api_topics(self) -> dict:
        cfg = _portal_config()
        latest = _latest_date()
        gm = _group_map()
        topics = []
        for key, t in (cfg.get("topics") or {}).items():
            products = []
            for p in t.get("products") or []:
                series = _trend_series(p.get("product"), p.get("category"))
                if not series:
                    continue
                s = series[0]
                pts = [x for x in s["points"] if x["average_price"] is not None]
                if not pts:
                    continue
                value = pts[-1]["average_price"]
                prev = pts[-2]["average_price"] if len(pts) >= 2 else None
                pct = round((value - prev) / prev * 100, 2) if prev and value is not None else None
                products.append({
                    "category": p.get("category"), "product": p.get("product"),
                    "unit": s["unit"], "value": value,
                    "change_pct": pct,
                    # sparkline 为兼容保留；spark_points 带日期，逐点可追溯
                    "sparkline": [x["average_price"] for x in pts[-7:]],
                    "spark_points": [{"date": x["price_date"],
                                      "value": x["average_price"]} for x in pts[-7:]],
                    "price_date": pts[-1]["price_date"],
                    "prev_date": pts[-2]["price_date"] if len(pts) >= 2 else None,
                    "explanation": f"{p.get('category')} · {p.get('product')}",
                })
            files = []
            if latest:
                for cat in t.get("categories") or []:
                    fp = _category_csv_path(cat, latest)
                    if fp.exists():
                        files.append({"category": cat,
                                      "path": f"/{fp.relative_to(EXPORTS_ROOT)}",
                                      "date": latest})
            topics.append({
                "key": key, "name": t.get("name", key), "icon": t.get("icon", ""),
                "color": t.get("color", "#2563eb"), "emphasis": bool(t.get("emphasis")),
                "description": t.get("description", ""),
                "categories": t.get("categories", []),
                "products": products,
                "files": files,
                "rankings": _rankings() if t.get("emphasis") else None,
            })
        return {
            "meta": {"generated_at": datetime.now().isoformat(timespec="seconds"), "source": "mixed"},
            "topics": topics,
        }

    def log_message(self, format, *args):
        """自定义日志格式。"""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{ts} - {self.client_address[0]} - {format % args}")


# ── 启动入口 ────────────────────────────────────────────

class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    """带 SO_REUSEADDR 的 TCP 服务器，避免重启时端口占用。"""
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    # 确保 exports 目录存在（ProtectSystem=strict 只读运行时可失败，忽略）
    try:
        EXPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[WARN] 无法创建导出目录（可能为只读运行）: {e}")

    # 认证模块与账号库检查（fail-closed：缺模块或 DB 不可用时拒绝启动，防无鉴权裸奔）
    if AuthStore is None:
        raise SystemExit(f"[ERROR] 认证模块导入失败: {AUTH_IMPORT_ERROR}")
    AUTH_STORE = AuthStore(str(AUTH_DB), _portal_config().get("auth") or {})
    try:
        AUTH_STORE.ensure_schema()
    except Exception as e:
        raise SystemExit(
            f"[ERROR] 认证数据库不可用（{AUTH_DB}）: {e}。"
            "请先运行 scripts/init_auth.py 初始化账号，"
            "并确认 systemd 单元已配置 StateDirectory=smm-fileserver。")

    with ReusableThreadingTCPServer(("0.0.0.0", PORT), DataCenterHandler) as httpd:
        # flush=True：stdout 非 TTY 时是块缓冲，不 flush 日志里看不到启动横幅
        print("锂电价格与回收业务数据中心已启动", flush=True)
        print(f"  地址: http://0.0.0.0:{PORT}", flush=True)
        print(f"  数据根目录: {EXPORTS_ROOT}", flush=True)
        print(f"  鉴权: 表单登录 + 服务端 Session（账号库: {AUTH_DB}）", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n服务器已停止")
