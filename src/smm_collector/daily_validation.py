"""每日统一数据验证层（9 层检查 + PASS/WARNING/FAIL 判定）。

流程定位：
    collector → raw data → validator(行级) → validated data → daily Excel
              → history → fixed summary

本模块在每日导出之后运行，对当日正式产物做文件级/结构级/业务级复核：

  1. file         文件级：每日 Excel/CSV/manifest 存在、可实际打开、非空
  2. schema       结构级：核心必填列（source/market/category/product_name/
                  specification/min/max/average_price/unit/price_date/
                  validation_status）必须存在
  3. date         日期级：price_date==target_date 对齐率（业务日期为准，绝不看 mtime）
  4. dedup        重复级：业务唯一键 (source,market,category,product_name,
                  specification,unit,price_date)；完全重复→计数（DB 已自动去重）；
                  同键不同价→conflict 进验证日志，绝不覆盖
  5. categories   缺失分类级：canonical 全集 vs 当日实际分类（expected/success/missing）
  6. numeric      数值级：min>max、负数、avg 出界、无法解析文本、invalid 占比
  7. volatility   波动级：同系列今日均价 vs 前一价格日，|日变化| 超阈值仅 warning
                  （阈值可配置、可按分类覆盖；市场价格大幅变化不拒绝数据）
  8. continuity   历史连续性级：序列在最近 N 个价格日中断 → missing history point
                  （仅提示；SMM 未发布不补值）
  9. fixed_summary 固定汇总一致性级：正式固定汇总行数 vs 已验证历史重建期望行数

设计原则：
  - 只读：不修改任何数据文件与数据库；验证失败绝不删除原始数据
  - 后台：结果写入 logs/validation/（不在 Web 根），用户页面不展示
  - 联动：FAIL 之日固定汇总不会被正式覆盖（exporter.update_summaries 门控强制），
    本模块负责记录与告警，不改动门控逻辑
"""
from __future__ import annotations
import csv
import json
import logging
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from openpyxl import load_workbook

from .config import load_portal_config
from .exporter import COLUMNS, build_summary_dataframe
from .validator import check_price_volatility

log = logging.getLogger("smm_collector.daily_validation")

# ── 结构验证的核心必填列（取自 exporter.COLUMNS 的 19 列子集） ──
CORE_COLUMNS = [
    "source", "market", "category", "product_name", "specification",
    "min_price", "max_price", "average_price", "unit", "price_date",
    "validation_status",
]

# ── 业务唯一键（与数据库 UNIQUE 约束一致） ──
BUSINESS_KEY_COLS = ["source", "market", "category", "product_name",
                     "specification", "unit", "price_date"]

DEFAULT_CONFIG = {
    "volatility_warning_pct": 20.0,   # |日变化| > 20% → warning（初始建议值）
    "volatility_error_pct": 100.0,    # > 100% → 仍只记 warning，不拒绝数据
    "continuity_window_days": 10,     # 历史连续性检查窗口（价格日数）
    "max_report_items": 20,           # 每个告警最多记录明细条数
    "log_dir": "logs/validation",     # 后台验证日志目录（不对公网开放）
    "category_overrides": {},         # 按分类覆盖波动阈值，如 {锂化合物: {warning_pct: 15}}
}

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DAILY_STEM = "SMM锂电现货价格"


def _merge_config(vcfg: dict | None) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if vcfg:
        cfg.update({k: v for k, v in vcfg.items() if v is not None})
    return cfg


def _issue(layer: str, level: str, message: str, detail: dict | None = None) -> dict:
    return {"layer": layer, "level": level, "message": message, "detail": detail or {}}


def _db_query(db_path: Path, sql: str, params=()) -> list[dict]:
    """只读 SQLite 查询（绝不写库）。"""
    con = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=30)
    try:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def _daily_paths(export_root: Path, target: str) -> dict:
    y, m = target[:4], target[5:7]
    base = export_root / y / m / "每日汇总"
    return {
        "xlsx": base / "Excel" / f"{DAILY_STEM}_{target}.xlsx",
        "csv": base / "CSV" / f"{DAILY_STEM}_{target}.csv",
        "manifest": base / f"SMM数据状态_{target}.json",
    }


def _load_manifest(export_root: Path, target: str) -> dict | None:
    try:
        return json.loads(_daily_paths(export_root, target)["manifest"].read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_daily_rows(export_root: Path, target: str) -> list[dict] | None:
    """读取每日总 CSV 全部行；文件缺失/损坏返回 None。"""
    p = _daily_paths(export_root, target)["csv"]
    if not p.exists():
        return None
    try:
        with p.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        log.warning("每日 CSV 读取失败: %s", e)
        return None


# ── 第 1 层：文件级验证 ──────────────────────────────────────────

def _check_files(export_root: Path, target: str, issues: list[dict]) -> dict:
    paths = _daily_paths(export_root, target)
    out = {"status": "ok", "checks": {}, "xlsx_data_rows": None, "csv_data_rows": None}

    p = paths["xlsx"]
    if not p.exists():
        issues.append(_issue("file", "error", "每日正式 Excel 缺失",
                             {"file": str(p.relative_to(export_root))}))
        out["status"] = "fail"
        out["checks"]["xlsx"] = {"exists": False}
    else:
        try:
            wb = load_workbook(p, read_only=True)
            ws = wb["全部数据"] if "全部数据" in wb.sheetnames else None
            n = ws.max_row if ws is not None else 0
            sheet_count = len(wb.sheetnames)
            wb.close()
            out["checks"]["xlsx"] = {"exists": True, "size": p.stat().st_size,
                                     "sheets": sheet_count,
                                     "data_rows": n - 1 if n else 0}
            if ws is None or n < 2:
                issues.append(_issue("file", "error", "每日 Excel 为空（全部数据 sheet 无有效数据行）",
                                     {"file": str(p.relative_to(export_root))}))
                out["status"] = "fail"
            else:
                out["xlsx_data_rows"] = n - 1
        except Exception as e:
            issues.append(_issue("file", "error", f"每日 Excel 损坏无法读取: {e}",
                                 {"file": str(p.relative_to(export_root))}))
            out["status"] = "fail"

    p = paths["csv"]
    if not p.exists():
        issues.append(_issue("file", "warning", "每日 CSV 缺失",
                             {"file": str(p.relative_to(export_root))}))
        out["checks"]["csv"] = {"exists": False}
        if out["status"] == "ok":
            out["status"] = "warn"
    else:
        try:
            with p.open(encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                next(reader, None)
                n = sum(1 for _ in reader)
            out["checks"]["csv"] = {"exists": True, "size": p.stat().st_size, "data_rows": n}
            out["csv_data_rows"] = n
            if n < 1:
                issues.append(_issue("file", "warning", "每日 CSV 为空",
                                     {"file": str(p.relative_to(export_root))}))
                if out["status"] == "ok":
                    out["status"] = "warn"
        except Exception as e:
            issues.append(_issue("file", "warning", f"每日 CSV 损坏无法读取: {e}",
                                 {"file": str(p.relative_to(export_root))}))
            if out["status"] == "ok":
                out["status"] = "warn"

    p = paths["manifest"]
    if not p.exists():
        issues.append(_issue("file", "warning", "数据状态 manifest 缺失",
                             {"file": str(p.relative_to(export_root))}))
        out["checks"]["manifest"] = {"exists": False}
        if out["status"] == "ok":
            out["status"] = "warn"
    elif _load_manifest(export_root, target) is None:
        issues.append(_issue("file", "warning", "数据状态 manifest 无法解析",
                             {"file": str(p.relative_to(export_root))}))
        out["checks"]["manifest"] = {"exists": True, "parses": False}
        if out["status"] == "ok":
            out["status"] = "warn"
    else:
        out["checks"]["manifest"] = {"exists": True, "parses": True}
    return out


# ── 第 2 层：结构验证 ────────────────────────────────────────────

def _check_schema(rows: list[dict] | None, issues: list[dict]) -> dict:
    out = {"status": "ok", "required_columns": CORE_COLUMNS, "missing_columns": []}
    if not rows:
        out["status"] = "skipped"
        return out
    cols = set(rows[0].keys())
    missing = [c for c in CORE_COLUMNS if c not in cols]
    if missing:
        issues.append(_issue("schema", "error", f"核心字段缺失: {', '.join(missing)}",
                             {"missing": missing}))
        out["status"] = "fail"
        out["missing_columns"] = missing
    return out


# ── 第 3 层：日期验证（业务日期，绝不使用文件 mtime） ──────────────

def _check_dates(rows: list[dict] | None, target: str, portal: dict | None,
                 issues: list[dict]) -> dict:
    gate = (portal or {}).get("summary_gate", {})
    min_align = float(gate.get("min_date_alignment_ratio", 0.6))
    out = {"status": "ok", "target_date": target, "alignment_ratio": None,
           "mismatched_rows": 0, "future_rows": 0}
    if not rows:
        out["status"] = "skipped"
        return out
    n = len(rows)
    align = sum(1 for r in rows if str(r.get("price_date", "")).strip()[:10] == target)
    future = sum(1 for r in rows if str(r.get("price_date", "")).strip()[:10] > target)
    ratio = align / n
    out["alignment_ratio"] = round(ratio, 4)
    out["mismatched_rows"] = n - align
    out["future_rows"] = future
    if future:
        issues.append(_issue("date", "warning", f"{future} 行价格日期晚于目标日期", {}))
        out["status"] = "warn"
    if ratio < min_align:
        if ratio == 0 and n >= 20:
            issues.append(_issue("date", "error",
                                 f"日期错误: 当日 {n} 行没有一行属于目标日期 {target}（对齐率 0）",
                                 {"alignment_ratio": 0.0}))
            out["status"] = "fail"
        else:
            issues.append(_issue("date", "warning",
                                 f"日期对齐率 {ratio:.1%} 低于阈值 {min_align:.0%}",
                                 {"alignment_ratio": round(ratio, 4)}))
            if out["status"] == "ok":
                out["status"] = "warn"
    return out


# ── 第 4 层：重复数据验证 ────────────────────────────────────────

def _check_duplicates(rows: list[dict] | None, issues: list[dict]) -> dict:
    out = {"status": "ok", "duplicate_rows": 0, "conflict_count": 0, "conflicts": []}
    if not rows:
        out["status"] = "skipped"
        return out
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        key = tuple(str(r.get(c, "") or "") for c in BUSINESS_KEY_COLS)
        groups[key].append(r)
    dup_count = 0
    conflicts = []
    for key, grp in groups.items():
        if len(grp) < 2:
            continue
        prices = {(str(r.get("min_price")), str(r.get("max_price")), str(r.get("average_price")))
                  for r in grp}
        if len(prices) == 1:
            dup_count += len(grp) - 1  # 完全重复：DB upsert 已自动去重，仅计数
        else:
            conflicts.append({
                "key": dict(zip(BUSINESS_KEY_COLS, key)),
                "prices": sorted(prices),
                "hashes": sorted({str(r.get("record_hash", "")) for r in grp}),
            })
    out["duplicate_rows"] = dup_count
    if dup_count:
        issues.append(_issue("dedup", "info", f"{dup_count} 行完全重复（业务唯一键同键同价，已自动去重）",
                             {"duplicate_rows": dup_count}))
    if conflicts:
        out["conflict_count"] = len(conflicts)
        out["conflicts"] = conflicts
        issues.append(_issue("dedup", "warning",
                             f"{len(conflicts)} 组同业务键但价格不同的冲突记录（不覆盖，需人工核对）",
                             {"conflicts": conflicts[:10], "total": len(conflicts)}))
        out["status"] = "warn"
    return out


# ── 第 5 层：缺失数据验证（expected vs actual） ──────────────────

def _check_categories(rows: list[dict] | None, portal: dict | None,
                      issues: list[dict]) -> dict:
    canonical = list((portal or {}).get("canonical_categories") or [])
    out = {"status": "ok", "expected_categories": canonical, "expected_count": len(canonical),
           "success_categories": [], "success_count": 0,
           "missing_count": 0, "missing_categories": []}
    if not rows:
        out["status"] = "skipped"
        return out
    actual = sorted({str(r.get("category", "")).strip()
                     for r in rows if str(r.get("category", "")).strip()})
    out["success_categories"] = actual
    out["success_count"] = len(actual)
    if not canonical:
        return out
    missing = sorted(set(canonical) - set(actual))
    out["missing_count"] = len(missing)
    out["missing_categories"] = missing
    if not actual:
        issues.append(_issue("categories", "error",
                             "核心分类全部缺失: 当日数据不含任何规范分类",
                             {"expected": canonical}))
        out["status"] = "fail"
    elif missing:
        head = "、".join(missing[:8]) + ("…" if len(missing) > 8 else "")
        issues.append(_issue("categories", "warning",
                             f"缺失分类 {len(missing)} 个: {head}",
                             {"missing": missing}))
        out["status"] = "warn"
    return out


# ── 第 6 层：数值合法性验证 ──────────────────────────────────────

def _is_number(v) -> bool:
    if v is None or str(v).strip() == "":
        return True  # 空值不算非法（由必填/行级校验把关）
    try:
        Decimal(str(v))
        return True
    except (InvalidOperation, ValueError):
        return False


def _check_numeric(rows: list[dict] | None, portal: dict | None,
                   issues: list[dict]) -> dict:
    gate = (portal or {}).get("summary_gate", {})
    max_invalid = float(gate.get("max_invalid_ratio", 0.05))
    out = {"status": "ok", "total_rows": len(rows) if rows else 0,
           "valid": 0, "warning": 0, "invalid": 0, "invalid_ratio": None,
           "non_numeric_cells": 0, "numeric_problems": {}}
    if not rows:
        out["status"] = "skipped"
        return out
    n = len(rows)
    invalid = [r for r in rows if r.get("validation_status") == "invalid"]
    warns = [r for r in rows if r.get("validation_status") == "warning"]
    out["valid"] = n - len(invalid) - len(warns)
    out["warning"] = len(warns)
    out["invalid"] = len(invalid)
    out["invalid_ratio"] = round(len(invalid) / n, 4)

    # 无法解析的价格文本（NaN / 文本 / 异常字符）
    bad_cells = 0
    for r in rows:
        for c in ("min_price", "max_price", "average_price"):
            if not _is_number(r.get(c)):
                bad_cells += 1
    out["non_numeric_cells"] = bad_cells

    problem_counter = Counter()
    warn_reason = Counter()
    for r in invalid + warns:
        msg = str(r.get("validation_message", ""))
        for label in ("最低价大于最高价", "为负数", "平均价不在最低价与最高价之间"):
            if label in msg:
                problem_counter[label] += 1
        # 行级 warning 原因汇总（如周更品种价格日期滞后），供后台排错
        reason = msg.split("；")[0].strip() or "未注明原因"
        warn_reason[reason[:40]] += 1
    if problem_counter:
        out["numeric_problems"] = dict(problem_counter)
    if warn_reason:
        out["warning_reasons"] = dict(warn_reason)

    if not invalid and not warns and bad_cells == 0:
        return out
    if len(invalid) == n:
        issues.append(_issue("numeric", "error",
                             f"数值校验失败: 当日全部 {n} 行均未通过",
                             {"invalid_ratio": 1.0}))
        out["status"] = "fail"
    elif out["invalid_ratio"] > max_invalid:
        issues.append(_issue("numeric", "warning",
                             f"invalid 行占比 {out['invalid_ratio']:.1%} 超过阈值 {max_invalid:.0%}",
                             {"invalid_ratio": out["invalid_ratio"]}))
        out["status"] = "warn"
    else:
        detail = dict(problem_counter)
        if bad_cells:
            detail["non_numeric_cells"] = bad_cells
        if warn_reason:
            detail["warning_reasons"] = dict(warn_reason)
        issues.append(_issue("numeric", "warning",
                             f"{len(invalid)} 行 invalid、{len(warns)} 行 warning"
                             + (f"、{bad_cells} 个无法解析价格单元格" if bad_cells else ""),
                             detail))
        out["status"] = "warn"
    return out


# ── 第 7 层：异常波动验证（只标 warning，不拒绝数据） ─────────────

def _series_key(r: dict) -> tuple:
    return (str(r.get("category", "")), str(r.get("product_name", "")),
            str(r.get("specification") or ""), str(r.get("unit") or ""))


def _check_volatility(db_path: Path, target: str, cfg: dict, issues: list[dict]) -> dict:
    warn_pct = float(cfg["volatility_warning_pct"])
    err_pct = float(cfg["volatility_error_pct"])
    overrides = cfg.get("category_overrides") or {}
    out = {"status": "ok", "prev_price_date": None, "compared_series": 0,
           "hits": [], "thresholds": {"warning_pct": warn_pct, "error_pct": err_pct}}
    if not db_path or not db_path.exists():
        out["status"] = "skipped"
        return out
    today = _db_query(db_path, "SELECT * FROM lithium_spot_prices WHERE price_date=?", (target,))
    if not today:
        out["status"] = "skipped"
        return out
    prev = _db_query(db_path, "SELECT MAX(price_date) AS d FROM lithium_spot_prices "
                              "WHERE price_date < ?", (target,))
    prev_d = prev[0].get("d") if prev else None
    if not prev_d:
        out["status"] = "skipped"
        return out
    out["prev_price_date"] = prev_d
    prev_rows = _db_query(db_path, "SELECT * FROM lithium_spot_prices WHERE price_date=?", (prev_d,))
    prev_map = {_series_key(r): r for r in prev_rows if r.get("average_price") is not None}

    def thresholds_for(category: str) -> tuple[float, float]:
        o = overrides.get(category)
        if isinstance(o, dict):
            return (float(o.get("warning_pct", warn_pct)), float(o.get("error_pct", err_pct)))
        return (warn_pct, err_pct)

    hits, seen = [], set()
    for r in today:
        if r.get("average_price") is None:
            continue
        k = _series_key(r)
        if k in seen or k not in prev_map:
            continue
        seen.add(k)
        cur, pre = Decimal(str(r["average_price"])), Decimal(str(prev_map[k]["average_price"]))
        wp, ep = thresholds_for(str(r.get("category", "")))
        # check_price_volatility 的阈值语义是比率（0.30=30%），配置值是百分数 → 除以 100
        level, ratio = check_price_volatility(cur, pre, warn_threshold=wp / 100.0,
                                              error_threshold=ep / 100.0)
        if level:
            hits.append({"category": r["category"], "product": r["product_name"],
                         "specification": r["specification"], "unit": r["unit"],
                         "today": str(cur), "prev_date": prev_d, "prev": str(pre),
                         "pct_change": round(ratio, 4) if ratio is not None else None,
                         "level": level})
    out["compared_series"] = len(seen)
    out["hits"] = hits
    if hits:
        issues.append(_issue("volatility", "warning",
                             f"{len(hits)} 个价格序列日波动超过阈值（仅提示，不删除数据）",
                             {"prev_price_date": prev_d,
                              "hits": hits[:cfg["max_report_items"]],
                              "total_hits": len(hits)}))
        out["status"] = "warn"
    return out


# ── 第 8 层：历史连续性验证（仅提示，不补值） ────────────────────

def _check_continuity(db_path: Path, target: str, cfg: dict, issues: list[dict]) -> dict:
    window_days = max(int(cfg["continuity_window_days"]), 2)
    out = {"status": "ok", "window_price_dates": [], "gap_series": 0, "gaps": []}
    if not db_path or not db_path.exists():
        out["status"] = "skipped"
        return out
    today_keys = {_series_key(r) for r in
                  _db_query(db_path, "SELECT DISTINCT category, product_name, "
                                     "specification, unit FROM lithium_spot_prices "
                                     "WHERE price_date=?", (target,))}
    if not today_keys:
        out["status"] = "skipped"
        return out
    dates = _db_query(db_path, "SELECT DISTINCT price_date FROM lithium_spot_prices "
                               "WHERE price_date <= ? ORDER BY price_date DESC LIMIT ?",
                      (target, window_days + 1))
    if len(dates) < 2:
        return out
    window = sorted(d["price_date"] for d in dates)
    out["window_price_dates"] = window
    window_min = window[0]
    rows = _db_query(db_path, "SELECT category, product_name, specification, unit, "
                              "price_date FROM lithium_spot_prices "
                              "WHERE price_date >= ? AND price_date <= ?",
                     (window_min, target))
    series_dates: dict[tuple, set] = defaultdict(set)
    for r in rows:
        series_dates[_series_key(r)].add(r["price_date"])
    gaps = []
    for k, ds in series_dates.items():
        if k not in today_keys or len(ds) < 2:
            continue
        first = min(ds)
        missing = [d for d in window if first < d < target and d not in ds]
        if missing:
            gaps.append({"category": k[0], "product": k[1], "specification": k[2],
                         "unit": k[3], "missing_price_dates": missing})
    out["gap_series"] = len(gaps)
    out["gaps"] = gaps
    if gaps:
        issues.append(_issue("continuity", "warning",
                             f"{len(gaps)} 个序列在最近 {window_days} 个价格日出现历史中断"
                             "（missing history point，仅提示；SMM 未发布不补值）",
                             {"gaps": gaps[:cfg["max_report_items"]], "total": len(gaps)}))
        out["status"] = "warn"
    return out


# ── 第 9 层：固定汇总一致性验证 ─────────────────────────────────

def _check_fixed_summary(db_path: Path, export_root: Path, issues: list[dict]) -> dict:
    out = {"status": "ok", "formal_exists": False, "formal_rows": None, "expected_rows": None}
    formal = export_root / "固定汇总" / f"{DAILY_STEM}_固定汇总.xlsx"
    if not formal.exists():
        issues.append(_issue("fixed_summary", "warning",
                             "正式固定汇总不存在",
                             {"file": str(formal.relative_to(export_root))}))
        out["status"] = "warn"
        return out
    out["formal_exists"] = True
    try:
        wb = load_workbook(formal, read_only=True)
        ws = wb["全部数据"] if "全部数据" in wb.sheetnames else None
        out["formal_rows"] = (ws.max_row - 1) if ws is not None else 0
        wb.close()
    except Exception as e:
        issues.append(_issue("fixed_summary", "warning", f"正式固定汇总无法读取: {e}",
                             {"file": str(formal.relative_to(export_root))}))
        out["status"] = "warn"
        return out
    if not db_path or not db_path.exists():
        return out
    # 期望值 = 已验证历史（剔除 invalid + 质量感知去重）重建的行数
    all_rows = _db_query(db_path, "SELECT * FROM lithium_spot_prices")
    expected = len(build_summary_dataframe(all_rows)) if all_rows else 0
    out["expected_rows"] = expected
    if out["formal_rows"] != expected:
        issues.append(_issue("fixed_summary", "warning",
                             f"固定汇总与已验证历史不一致（正式 {out['formal_rows']} 行 vs 期望 {expected} 行），"
                             "建议运行 scripts/rebuild_summaries.py 重建",
                             {"formal_rows": out["formal_rows"], "expected_rows": expected}))
        out["status"] = "warn"
    return out


# ── 汇总与日志 ──────────────────────────────────────────────────

def _write_logs(result: dict, cfg: dict) -> Path:
    p = Path(cfg["log_dir"])
    log_dir = p if p.is_absolute() else _PROJECT_ROOT / p
    log_dir.mkdir(parents=True, exist_ok=True)
    jpath = log_dir / f"validation_{result['date']}.json"
    jpath.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                     encoding="utf-8")
    counts = result["counts"]
    line = (f"{result['generated_at']} {result['date']} VERDICT={result['verdict']} "
            f"errors={counts['error']} warnings={counts['warning']} info={counts['info']}"
            + (" | " + " | ".join(i["message"] for i in result["issues"][:5]) if result["issues"] else "")
            + "\n")
    with (log_dir / "validation.log").open("a", encoding="utf-8") as f:
        f.write(line)
    return jpath


def run_daily_validation(target_date, db_path=None, export_root=None,
                         portal_cfg=None, vcfg=None) -> dict:
    """对指定业务日期执行 9 层验证，返回结果并写 logs/validation/。

    参数：
      target_date: str|date 业务日期（YYYY-MM-DD）
      db_path: SQLite 路径（只读打开，默认 data/database/smm_lithium.db）
      export_root: 导出根目录（默认 data/exports）
      portal_cfg: categories_portal.yaml 内容（默认自动读取）
      vcfg: settings.yaml 的 validation 段（阈值等，默认取 DEFAULT_CONFIG）
    """
    cfg = _merge_config(vcfg)
    target = str(target_date)
    export_root = Path(export_root or _PROJECT_ROOT / "data" / "exports")
    db_path = Path(db_path or _PROJECT_ROOT / "data" / "database" / "smm_lithium.db")
    portal = portal_cfg if portal_cfg is not None else load_portal_config(_PROJECT_ROOT)

    issues: list[dict] = []
    rows = _load_daily_rows(export_root, target)
    layers = {
        "file": _check_files(export_root, target, issues),
        "schema": _check_schema(rows, issues),
        "date": _check_dates(rows, target, portal, issues),
        "dedup": _check_duplicates(rows, issues),
        "categories": _check_categories(rows, portal, issues),
        "numeric": _check_numeric(rows, portal, issues),
        "volatility": _check_volatility(db_path, target, cfg, issues),
        "continuity": _check_continuity(db_path, target, cfg, issues),
        "fixed_summary": _check_fixed_summary(db_path, export_root, issues),
    }
    counts = Counter(i["level"] for i in issues)
    verdict = ("FAIL" if counts["error"] else
               "WARNING" if (counts["warning"] or counts["error"]) else "PASS")
    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "date": target,
        "verdict": verdict,
        "layers": layers,
        "issues": issues,
        "counts": {"error": counts["error"], "warning": counts["warning"], "info": counts["info"]},
    }
    result["log_file"] = str(_write_logs(result, cfg))
    log.info("每日验证 %s date=%s errors=%d warnings=%d",
             verdict, target, counts["error"], counts["warning"])
    return result


if __name__ == "__main__":
    import argparse
    from datetime import date
    p = argparse.ArgumentParser(description="SMM 每日数据验证（后台，结果写 logs/validation/）")
    p.add_argument("--date", type=date.fromisoformat, default=date.today())
    args = p.parse_args()
    res = run_daily_validation(args.date.isoformat())
    print(f"date={res['date']} verdict={res['verdict']} "
          f"errors={res['counts']['error']} warnings={res['counts']['warning']}")
    print(f"log={res['log_file']}")
    for i in res["issues"]:
        if i["level"] != "info":
            print(f"  [{i['level']}] {i['layer']}: {i['message']}")
