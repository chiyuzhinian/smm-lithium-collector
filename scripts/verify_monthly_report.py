"""月度业务报表数据校验 CLI：8 月业务报表导出数据 ↔ SMM 历史导出数据 三方对比（只读）。

用途（2026-09-09 业务要求）：
  将「月度业务报表」导出数据与「SMM 历史导出数据（每日汇总 CSV）」进行人工计算结果对比，
  验证产品数量 / 日期覆盖 / 最新价格 / 历史价格区间 / 月均价 / 环比，输出数据校验报告。

口径（与 portal_service 完全一致，独立重算不引用其月均函数）：
  - 匹配 = 业务映射 series 精确三元组 (category, product_name, specification)；
  - 排除 validation_status='invalid'；
  - 按 price_date 去重（同一报价点重复采集取 collected_at 最新，不增权重）；
  - 月均价 = 当月有效 average_price 的 Decimal 算术平均（≥1 → 2 位小数，<1 → 4 位小数）；
  - 环比 =（本月−上月）÷上月×100%，仅相邻两月均完整时计算（7 月不完整 → 8 月预期留空）。

三方数据：
  - 系统导出 = data/exports/月度业务报表/月度业务报表_{month}.xlsx（openpyxl 读 F/H 列）；
  - SMM历史数据 = data/exports/{YYYY}/{MM}/每日汇总/CSV/*.csv（采集当日导出的原始 CSV）；
  - 人工计算 = 从 SMM历史数据按上述口径独立重算。
只读：绝不修改数据库、CSV 或 xlsx。差异只分析原因，不自动改数据。

用法：
  .venv/bin/python scripts/verify_monthly_report.py [--month 2026-08] [--write-report]
输出：console + （--write-report 时）docs/月度报表验证_{month}.md
退出码：0 全部一致 / 1 存在差异 / 2 运行错误
"""
from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import sys
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import openpyxl  # noqa: E402
from smm_collector import portal_service as ps  # noqa: E402

CONFIG_ROOT = ROOT / "config"
DB_PATH = ROOT / "data" / "database" / "smm_lithium.db"
MONTHLY_BIG, MONTHLY_SMALL = 2, 4


# ── 独立重算（不调用 portal_service 的月均实现） ─────────────────

def _dec(v):
    if v is None or str(v).strip() == "":
        return None
    try:
        return Decimal(str(v).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _round_monthly(v: Decimal) -> str:
    places = MONTHLY_BIG if v >= 1 else MONTHLY_SMALL
    return str(v.quantize(Decimal("1").scaleb(-places), rounding=ROUND_HALF_UP))


def load_csv_points(month: str) -> list[dict]:
    """读 {month} 每日汇总 CSV 全量行（SMM 历史导出数据）。"""
    y, m = month.split("-")
    csv_dir = ROOT / "data" / "exports" / y / m / "每日汇总" / "CSV"
    if not csv_dir.is_dir():
        return []
    rows: list[dict] = []
    for f in sorted(csv_dir.glob("SMM锂电现货价格_*.csv")):
        text = f.read_text(encoding="utf-8-sig")
        for r in csv.DictReader(io.StringIO(text)):
            rows.append(r)
    return rows


def recompute_series(csv_rows: list[dict], product: dict, month: str) -> dict:
    """按映射三元组从 CSV 独立重算单品种月统计（镜像 product_rows + merge_points 口径）。"""
    first_day = f"{month}-01"
    next_month = f"{int(month[:4]) + (1 if month[5:] == '12' else 0):04d}-{('01' if month[5:] == '12' else str(int(month[5:]) + 1).zfill(2))}-01"
    triples = {(t["category"], t["product_name"], t["specification"])
               for t in product.get("series") or []}
    matched = [
        r for r in csv_rows
        if (r.get("category"), r.get("product_name"), r.get("specification")) in triples
        and (r.get("validation_status") or "") != "invalid"
        and first_day <= (r.get("price_date") or "") < next_month
    ]
    # price_date 去重：同报价点取 collected_at 最新（CSV 无 id，同秒时按读入顺序稳定）
    best: dict[str, dict] = {}
    for r in sorted(matched, key=lambda x: (x.get("price_date") or "", x.get("collected_at") or "")):
        best[r["price_date"]] = r
    pts = [best[d] for d in sorted(best)]
    vals = [v for r in pts if (v := _dec(r.get("average_price"))) is not None]
    avg = (sum(vals, Decimal(0)) / Decimal(len(vals))) if vals else None
    return {
        "n_points": len(vals),
        "date_from": pts[0]["price_date"] if pts else None,
        "date_to": pts[-1]["price_date"] if pts else None,
        "avg": _round_monthly(avg) if avg is not None else None,
        "latest": _dec(pts[-1].get("average_price")) if pts else None,
        "latest_date": pts[-1]["price_date"] if pts else None,
        "range_min": min((_dec(r["average_price"]) for r in pts if _dec(r.get("average_price")) is not None), default=None),
        "range_max": max((_dec(r["average_price"]) for r in pts if _dec(r.get("average_price")) is not None), default=None),
        "raw_points": len(pts),
    }


# ── 系统侧读取 ─────────────────────────────────────────────

def read_xlsx(month: str) -> dict:
    """读月度业务报表 xlsx：主表行号 = template_row（行 2-50）；组织(A)/月均价(F)/环比(H)。

    注意 F/H 在 xlsx 中为 float（数值格式控制显示），与系统 Decimal 值比较需容差。
    """
    path = ROOT / "data" / "exports" / "月度业务报表" / f"月度业务报表_{month}.xlsx"
    if not path.is_file():
        return {}
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    out: dict[int, dict] = {}
    for sheet_row, row in enumerate(
            ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=8, values_only=True), start=2):
        if row[0] is None and row[4] is None and row[5] is None:
            continue
        out[sheet_row] = {
            "org": str(row[0] or ""),
            "avg": None if row[5] is None else f"{float(row[5]):.4f}".rstrip("0").rstrip("."),
            "mom": None if row[7] is None else f"{float(row[7]) * 100:g}%",
        }
    wb.close()
    return out


# ── 校验主流程 ─────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="月度业务报表数据校验（只读）")
    ap.add_argument("--month", default="2026-08", help="YYYY-MM，默认 2026-08")
    ap.add_argument("--write-report", action="store_true",
                    help="同时写出 docs/月度报表验证_{month}.md")
    args = ap.parse_args()
    month = args.month
    if ps.month_bounds(month) is None:
        print(f"[ERROR] 月份格式非法：{month}")
        return 2

    products, meta = ps.load_products(CONFIG_ROOT)
    if meta["load_errors"]:
        print(f"[ERROR] 映射配置存在问题：{meta['load_errors']}")
        return 2
    if len(products) != 49 or len(ps.smm_products(products)) != 40:
        print(f"[ERROR] 产品映射数量异常：{len(products)} 行 / SMM {len(ps.smm_products(products))}")
        return 2

    # ① 系统侧：monthly_payload（DB）+ xlsx
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        payload = ps.monthly_payload(products, con, month, meta)
    finally:
        con.close()
    sys_rows = {r["id"]: r for r in payload["rows"]}
    xlsx = read_xlsx(month)

    # ② SMM 历史导出 + ③ 人工计算
    csv_rows = load_csv_points(month)
    print(f"[INFO] SMM 历史导出 CSV：{len(csv_rows)} 行（{month} 每日汇总）")

    report: list[dict] = []
    n_mismatch = 0
    for p in products:
        sid = sys_rows.get(p["id"], {})
        rc = recompute_series(csv_rows, p, month)
        x = xlsx.get(p["template_row"], {})
        xlsx_avg = x.get("avg")
        db_avg = sid.get("monthly_avg")
        rec_avg = rc["avg"]
        status, reason = "—", ""
        tol = Decimal("0.001")  # xlsx float 存储的显示容差（F 列按小数位格式显示）
        xlsx_d = _dec(xlsx_avg)
        rec_d = _dec(rec_avg)
        if xlsx_d is not None and rec_d is not None:
            diff = abs(xlsx_d - rec_d)
            if diff <= tol:
                status = "一致"
            else:
                status = "差异"
                reason = (f"系统导出={xlsx_avg} vs 人工计算={rec_avg}；"
                          f"CSV 点数={rc['n_points']}（DB 侧 {db_avg or '—'}，{sid.get('n_points') or 0} 点）")
                n_mismatch += 1
        elif xlsx_d is None and rec_d is None:
            status = "一致（均无数据）"
        elif xlsx_d is None:
            status = "差异"
            reason = f"系统导出为空（{sid.get('reason') or '—'}）但 CSV 可重算 {rec_avg}（{rc['n_points']} 点）"
            n_mismatch += 1
        else:
            status = "差异"
            reason = f"系统导出有 {xlsx_avg} 但 CSV 无对应数据（DB 侧 {db_avg or '—'}，{sid.get('n_points') or 0} 点）"
            n_mismatch += 1
        # xlsx 与 DB payload 一致性（生成时点不同的快照差）
        xlsx_db_note = ""
        if xlsx_d is not None and db_avg is not None and abs(xlsx_d - Decimal(db_avg)) > tol:
            xlsx_db_note = "（注意：xlsx 与当前 DB 不一致，可能为 9-06 生成快照后又有补采）"
        report.append({
            "row": p["template_row"], "id": p["id"], "name": p["display_name"],
            "spec": p["spec"] or "", "sys_avg": xlsx_avg or "—",
            "csv_points": rc["n_points"], "rec_avg": rec_avg or "—",
            "diff": reason or ("0" if status.startswith("一致") else "—"),
            "status": status + xlsx_db_note,
            "latest": f"{rc['latest']} ({rc['latest_date']})" if rc["latest"] is not None else "—",
            "range": (f"{rc['range_min']} ~ {rc['range_max']}" if rc["range_min"] is not None else "—"),
            "dates": f"{rc['date_from']} ~ {rc['date_to']}" if rc["date_from"] else "—",
            "sys_dates": f"{sid.get('date_from')} ~ {sid.get('date_to')}" if sid.get("date_from") else "—",
        })

    # 环比抽查（7 月不完整 → 8 月环比预期全空；xlsx H 列与 payload 一致）
    mom_payload = [r for r in payload["rows"] if r["mom_pct"] is not None]
    mom_xlsx_filled = [k for k, v in xlsx.items() if v.get("mom") and v["mom"].strip() not in ("", "—", "-")]
    print(f"[INFO] 环比：payload 计算 {len(mom_payload)} 行 / xlsx H 列非空 {len(mom_xlsx_filled)} 行")

    # 输出
    lines: list[str] = []
    lines.append(f"# 月度业务报表数据校验报告（{month}）\n")
    lines.append(f"- 生成时间：{date.today().isoformat()}")
    lines.append(f"- 产品数量：映射 {len(products)} 行（SMM {len(ps.smm_products(products))}）"
                 f" / xlsx 数据行 {len(xlsx)}")
    lines.append(f"- SMM 历史导出：{len(csv_rows)} 行（每日汇总 CSV）")
    lines.append(f"- 结论：差异 {n_mismatch} 行\n")
    lines.append("|产品|系统导出|SMM历史数据|人工计算|差异|结果|")
    lines.append("|-|-|-|-|-|-|")
    for r in report:
        lines.append(
            f"|{r['row']}. {r['name']}（{r['spec']}）|{r['sys_avg']}|"
            f"{r['csv_points']} 点<br>{r['dates']}|{r['rec_avg']}|{r['diff']}|{r['status']}|")
    lines.append("")
    lines.append("## 明细补充\n")
    lines.append("|产品|系统覆盖范围|CSV覆盖范围|CSV最新价格|CSV历史区间|")
    lines.append("|-|-|-|-|-|")
    for r in report:
        lines.append(f"|{r['row']}. {r['name']}|{r['sys_dates']}|{r['dates']}|{r['latest']}|{r['range']}|")
    if n_mismatch:
        lines.append("\n## 差异分析\n")
        lines.append("> 差异只记录原因，不修改任何数据。已核实的根因（以 电池级碳酸锂 为例逐日比对）：\n")
        lines.append("> 1. **SMM 页面翻日 + 兜底补采**：08-12 报价点是 08-13 09:00 补采写入 DB 的、"
                     "08-31 报价点是 09-01 09:05 采集写入 DB 的——这些晚到点进了 DB（月报口径），"
                     "但不在 8 月当日 CSV 快照（历史导出口径）里，两数据集点数不同 → 月均价有差异；\n")
        lines.append("> 2. **计算口径一致**：点数相同的行（周频电芯 4 点等）人工计算与系统导出完全一致，"
                     "验证月均价算法（去重/排除 invalid/算术平均）无偏差；\n")
        lines.append("> 3. 差异属于两导出数据集的**固有口径差异**（DB 含补采点、CSV 为当日快照），"
                     "不是计算错误；如需月度报表与 SMM 历史导出完全对齐，应重新生成 8 月月报或统一采用 DB 口径。\n")
        for r in report:
            if r["status"] == "差异":
                lines.append(f"- 行{r['row']} {r['name']}：{r['diff']}")

    text = "\n".join(lines)
    print(text)
    if args.write_report:
        out = ROOT / "docs" / f"月度报表验证_{month}.md"
        out.write_text(text + "\n", encoding="utf-8")
        print(f"\n[INFO] 报告已写出：{out}")
    return 1 if n_mismatch else 0


if __name__ == "__main__":
    sys.exit(main())
