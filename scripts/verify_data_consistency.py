#!/usr/bin/env python3
"""门户数据一致性只读校验脚本。

用途：核验「数据库真实采集记录」与「门户 API 计算值 / 导出文件」的一致性。
原则：只读、只比较、只报告——绝不修改价格、绝不补数据、绝不删数据。

检查项：
  1. 首页指标最新价   vs DB 独立重算（按产品最新有效日期聚合）
  2. 涨跌幅           vs DB 相邻两个有效日期计算值（另与官方 change_value 比对）
  3. 7日趋势          vs DB 最近 7 个真实日期（断言无填充、点数=min(7,真实日期数)）
  4. 涨幅榜           vs DB 重算（全部 pct>0、降序、≤5 项）
  5. 跌幅榜           vs DB 重算（全部 pct<0、升序、≤5 项；出现 pct>=0 即 ERROR）
  6. 导出一致性       vs 最新日期每日 CSV 行级比对
  7. 历史一致性       导出日期集 vs DB 日期集（反向缺口 ERROR / 正向缺口 WARNING）

退出码：0=全部 PASS；1=存在 ERROR；2=仅存在 WARNING（无 ERROR）。
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import file_server as fs  # noqa: E402  门户模块（import 安全，服务启动在 __main__ 守卫内）

TOL = 1e-6
DATE_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"

# 独立参考 SQL：与门户代码分开实现，用于互相对账（按 collected_at 最新去重每 (日期,产品,规格)）
_DAY_AVG_SQL = """
WITH ranked AS (
  SELECT price_date, product_name, category, specification, average_price,
         ROW_NUMBER() OVER (
           PARTITION BY price_date, product_name, specification
           ORDER BY collected_at DESC, id DESC) AS rn
  FROM lithium_spot_prices
  WHERE validation_status != 'invalid'
    AND price_date GLOB '%s')
SELECT price_date, AVG(CAST(average_price AS REAL)) AS day_avg
FROM ranked
WHERE rn = 1 AND average_price IS NOT NULL
{filter}
GROUP BY price_date ORDER BY price_date
""" % DATE_GLOB


class Reporter:
    """收集 PASS/ERROR/WARNING 并输出报告。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.items: list[tuple[str, str]] = []  # (status, message)

    def add(self, status: str, message: str) -> None:
        assert status in ("PASS", "ERROR", "WARNING")
        self.items.append((status, message))

    def counts(self) -> tuple[int, int, int]:
        return (sum(1 for s, _ in self.items if s == "PASS"),
                sum(1 for s, _ in self.items if s == "ERROR"),
                sum(1 for s, _ in self.items if s == "WARNING"))

    def exit_code(self) -> int:
        _, err, warn = self.counts()
        return 1 if err else (2 if warn else 0)

    def print_report(self) -> None:
        print("=== 门户数据一致性校验（只读，不修改任何数据） ===")
        print(f"数据库: {self.db_path} (mode=ro)")
        print(f"检查时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("-" * 70)
        for status, message in self.items:
            print(f"[{status}] {message}")
        ok, err, warn = self.counts()
        print("-" * 70)
        print(f"结果: {ok} PASS / {err} ERROR / {warn} WARNING → 退出码 {self.exit_code()}")

    def to_json(self) -> str:
        return json.dumps({
            "db": self.db_path,
            "results": [{"status": s, "message": m} for s, m in self.items],
            "counts": {"pass": self.counts()[0], "error": self.counts()[1],
                       "warning": self.counts()[2]},
            "exit_code": self.exit_code(),
        }, ensure_ascii=False, indent=2)


def _db_ro(db_path: str) -> sqlite3.Connection:
    """只读连接（URI mode=ro，物理上无法写）。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _close(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) <= TOL
    except (TypeError, ValueError):
        return False


def ref_day_avgs(con, product=None, category=None) -> dict[str, float]:
    """独立参考值：每日期均价（与门户 _metrics 的聚合口径对应）。"""
    if product:
        sql = _DAY_AVG_SQL.format(filter="AND product_name = ?")
        params: tuple = (product,)
    elif category:
        sql = _DAY_AVG_SQL.format(filter="AND category = ?")
        params = (category,)
    else:
        sql = _DAY_AVG_SQL.format(filter="")
        params = ()
    return {str(r["price_date"]): round(float(r["day_avg"]), 4)
            for r in con.execute(sql, params)}


def ref_spec_count_latest(con, product=None, category=None) -> int:
    """最新日期上该产品/分类的规格数（用于 change_value 官方涨跌额比对：单规格才有意义）。"""
    if product:
        cond, params = "AND product_name = ?", (product,)
    elif category:
        cond, params = "AND category = ?", (category,)
    else:
        cond, params = "", ()
    row = con.execute(
        f"SELECT COUNT(DISTINCT specification) AS n FROM lithium_spot_prices "
        f"WHERE validation_status != 'invalid' AND price_date GLOB '{DATE_GLOB}' "
        f"AND price_date = (SELECT MAX(price_date) FROM lithium_spot_prices "
        f"WHERE validation_status != 'invalid' AND price_date GLOB '{DATE_GLOB}' {cond}) {cond}",
        (*params, *params)).fetchone()
    return int(row["n"]) if row else 0


def ref_change_value(con, product=None, category=None) -> float | None:
    """最新日期该产品/分类全部行的 change_value 列表（单规格时用于官方值比对）。"""
    if product:
        cond, params = "AND product_name = ?", (product,)
    elif category:
        cond, params = "AND category = ?", (category,)
    else:
        cond, params = "", ()
    rows = con.execute(
        f"SELECT change_value FROM lithium_spot_prices "
        f"WHERE validation_status != 'invalid' AND price_date GLOB '{DATE_GLOB}' "
        f"AND price_date = (SELECT MAX(price_date) FROM lithium_spot_prices "
        f"WHERE validation_status != 'invalid' AND price_date GLOB '{DATE_GLOB}' {cond}) {cond}",
        (*params, *params)).fetchall()
    vals = []
    for r in rows:
        try:
            vals.append(float(r["change_value"]))
        except (TypeError, ValueError):
            pass
    return vals[0] if len(vals) == 1 else None


def ref_rank_changes(con, as_of: str) -> list[dict]:
    """独立重算涨跌榜窗口变化（与门户 _rankings 相同口径，但独立 SQL 实现）。"""
    rows = con.execute(
        f"""WITH window_dates AS (
              SELECT DISTINCT price_date FROM lithium_spot_prices
              WHERE price_date GLOB '{DATE_GLOB}' AND price_date < ?
              ORDER BY price_date DESC LIMIT 2),
            ranked AS (
              SELECT price_date, category, product_name, specification, unit, average_price,
                     ROW_NUMBER() OVER (
                       PARTITION BY price_date, category, product_name, specification, unit
                       ORDER BY collected_at DESC, id DESC) AS rn
              FROM lithium_spot_prices WHERE validation_status != 'invalid')
            SELECT category, product_name, specification, unit, price_date, average_price
            FROM ranked WHERE rn = 1 AND price_date IN (SELECT price_date FROM window_dates)
            ORDER BY price_date""", (as_of,)).fetchall()
    by_key: dict = {}
    for r in rows:
        try:
            avg = float(r["average_price"])
        except (TypeError, ValueError):
            continue
        key = (r["category"], r["product_name"], r["specification"] or "", r["unit"] or "")
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
                        "unit": unit, "value": last_v, "pct": pct,
                        "prev_date": ds[-2], "date": ds[-1]})
    return changes


def check_metrics(reporter: Reporter, con: sqlite3.Connection) -> None:
    """检查1/2/3：指标最新价、涨跌幅、7日趋势 vs DB 独立重算。"""
    global_latest = fs._db_latest_date()
    metrics = fs._metrics(global_latest)
    if not metrics:
        reporter.add("ERROR", "指标最新价: _metrics 返回空（配置无指标或 DB 无数据）")
        return
    ok_value = ok_pct = ok_spark = 0
    for m in metrics:
        name = m.get("name") or m.get("product") or m.get("category")
        ref = ref_day_avgs(con, m.get("product"), m.get("category"))
        if not ref:
            reporter.add("ERROR", f"指标最新价: {name} 参考查询无数据（DB 与 API 不一致）")
            continue
        ref_dates = sorted(ref)
        ref_latest, ref_prev = ref_dates[-1], (ref_dates[-2] if len(ref_dates) >= 2 else None)
        # 检查1：最新价 + 日期
        if m["price_date"] != ref_latest or not _close(m["value"], ref[ref_latest]):
            reporter.add("ERROR", f"指标最新价: {name} API={m['value']}@{m['price_date']} "
                                  f"DB={ref[ref_latest]}@{ref_latest}")
        else:
            ok_value += 1
        # 检查2：涨跌幅
        if ref_prev is None:
            if m["change_pct"] is not None or m["prev_date"] is not None:
                reporter.add("ERROR", f"涨跌幅: {name} 仅 1 个数据日期，API 却返回了涨跌幅")
            else:
                ok_pct += 1
        else:
            expect = round((ref[ref_latest] - ref[ref_prev]) / ref[ref_prev] * 100, 2)
            if m["prev_date"] != ref_prev or not _close(m["change_pct"], expect):
                reporter.add("ERROR", f"涨跌幅: {name} API={m['change_pct']} "
                                      f"(prev {m['prev_date']}) DB={expect} (prev {ref_prev})")
            else:
                ok_pct += 1
            # 官方 change_value 一致性（单规格才有意义，多规格为聚合口径跳过）
            if ref_spec_count_latest(con, m.get("product"), m.get("category")) == 1:
                official = ref_change_value(con, m.get("product"), m.get("category"))
                if official is not None and m["change"] is not None \
                        and not _close(m["change"], official):
                    reporter.add("WARNING", f"官方涨跌额: {name} 计算涨跌 {m['change']} "
                                            f"vs DB change_value {official} 不一致")
        # 检查3：7日趋势（点数=min(7,真实日期数)，无填充）
        expect_spark = [{"date": d, "value": round(ref[d], 4)} for d in ref_dates[-7:]]
        got = m.get("spark_points") or []
        if len(got) != min(7, len(ref_dates)):
            reporter.add("ERROR", f"7日趋势: {name} 点数 {len(got)} != min(7,{len(ref_dates)})"
                                  f"（存在填充或截断）")
        elif all(_close(g["value"], e["value"]) and g["date"] == e["date"]
                 for g, e in zip(got, expect_spark)):
            ok_spark += 1
        else:
            reporter.add("ERROR", f"7日趋势: {name} 日期或值与 DB 不一致 "
                                  f"API={got} DB={expect_spark}")
        if len(got) < 2:
            reporter.add("WARNING", f"7日趋势: {name} 仅 {len(got)} 个真实数据点")
    total = len(metrics)
    reporter.add("PASS" if ok_value == total else "ERROR",
                 f"指标最新价: {ok_value}/{total} 与 DB 一致")
    reporter.add("PASS" if ok_pct == total else "ERROR",
                 f"涨跌幅: {ok_pct}/{total} 与 DB 相邻双日期计算一致")
    reporter.add("PASS" if ok_spark == total else "ERROR",
                 f"7日趋势: {ok_spark}/{total} spark_points 与 DB 一致（无填充）")


def check_rankings(reporter: Reporter, con: sqlite3.Connection, as_of: str) -> None:
    """检查4/5：涨跌榜规则 + 与 DB 独立重算一致。"""
    got = fs._rankings(as_of=as_of)
    ref = ref_rank_changes(con, as_of)
    ref_gainers = sorted((c for c in ref if c["pct"] > 0),
                         key=lambda x: x["pct"], reverse=True)[:5]
    ref_losers = sorted((c for c in ref if c["pct"] < 0),
                        key=lambda x: x["pct"])[:5]
    based = got.get("based_on") or {}
    range_label = f"（对比 {based.get('prev_date')} → {based.get('date')}）"
    # 规则校验
    g_ok = all(c["pct"] > 0 for c in got["top_gainers"]) and \
        all(got["top_gainers"][i]["pct"] >= got["top_gainers"][i + 1]["pct"]
            for i in range(len(got["top_gainers"]) - 1))
    l_ok = all(c["pct"] < 0 for c in got["top_losers"]) and \
        all(got["top_losers"][i]["pct"] <= got["top_losers"][i + 1]["pct"]
            for i in range(len(got["top_losers"]) - 1))
    if not g_ok:
        reporter.add("ERROR", f"涨幅榜: 存在 pct<=0 或未降序 {range_label}")
    else:
        reporter.add("PASS", f"涨幅榜: {len(got['top_gainers'])} 项，全部 pct>0 降序 {range_label}")
    if not l_ok:
        bad = [c for c in got["top_losers"] if c["pct"] >= 0]
        reporter.add("ERROR", f"跌幅榜: 规则违反——非负涨跌进入跌幅榜 {bad} {range_label}")
    else:
        reporter.add("PASS", f"跌幅榜: {len(got['top_losers'])} 项，全部 pct<0 升序 {range_label}")
    # 与参考重算一致（集合对比，pct 已两位小数）
    key = lambda c: (c["product"], c["specification"], c["pct"])  # noqa: E731
    if set(map(key, got["top_gainers"])) != set(map(key, ref_gainers)):
        reporter.add("ERROR", f"涨幅榜: API 与 DB 独立重算不一致 "
                              f"API={[key(c) for c in got['top_gainers']]} "
                              f"DB={[key(c) for c in ref_gainers]}")
    if set(map(key, got["top_losers"])) != set(map(key, ref_losers)):
        reporter.add("ERROR", f"跌幅榜: API 与 DB 独立重算不一致 "
                              f"API={[key(c) for c in got['top_losers']]} "
                              f"DB={[key(c) for c in ref_losers]}")


def check_exports(reporter: Reporter, con: sqlite3.Connection, exports_dir: Path) -> None:
    """检查6：最新日期每日 CSV 与 DB 行级一致。"""
    row = con.execute(
        f"SELECT MAX(price_date) AS d FROM lithium_spot_prices "
        f"WHERE price_date GLOB '{DATE_GLOB}'").fetchone()
    latest = str(row["d"]) if row and row["d"] else None
    if not latest:
        reporter.add("ERROR", "导出一致性: DB 无有效价格日期")
        return
    csv_path = exports_dir / latest[:4] / latest[5:7] / "每日汇总" / "CSV" / \
        f"{fs.DAILY_STEM}_{latest}.csv"
    if not csv_path.exists():
        reporter.add("ERROR", f"导出一致性: 缺少 {csv_path.relative_to(exports_dir)}")
        return
    db_rows = {
        (r["product_name"], r["specification"] or "", round(float(r["average_price"]), 4))
        for r in con.execute(
            "SELECT product_name, specification, average_price FROM lithium_spot_prices "
            "WHERE validation_status != 'invalid' AND price_date = ?", (latest,))
        if r["average_price"] is not None}
    csv_rows = set()
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if (r.get("price_date") or "").strip() != latest:
                continue
            if r.get("average_price") in (None, ""):
                continue
            try:
                csv_rows.add((r["product_name"], r["specification"] or "",
                              round(float(r["average_price"]), 4)))
            except ValueError:
                reporter.add("WARNING", f"导出一致性: CSV 行平均价无法解析: {r}")
    missing = db_rows - csv_rows
    extra = csv_rows - db_rows
    if missing or extra:
        reporter.add("ERROR", f"导出一致性: {latest} CSV 与 DB 不一致 "
                              f"DB有CSV无={len(missing)} CSV有DB无={len(extra)}")
    else:
        reporter.add("PASS", f"导出一致性: {latest} 每日 CSV {len(csv_rows)} 行与 DB 行级一致")


def check_history(reporter: Reporter, con: sqlite3.Connection, exports_dir: Path) -> None:
    """检查7：导出日期集 vs DB 日期集。"""
    db_dates = {str(r["price_date"]) for r in con.execute(
        f"SELECT DISTINCT price_date FROM lithium_spot_prices "
        f"WHERE price_date GLOB '{DATE_GLOB}'")}
    csv_dates = set()
    for p in exports_dir.glob("*/*/每日汇总/CSV/*.csv"):
        if p.stem.startswith(fs.DAILY_STEM + "_"):
            csv_dates.add(p.stem.rsplit("_", 1)[-1])
    reverse_gap = csv_dates - db_dates     # 有导出文件但 DB 无行 → 数据源断裂
    forward_gap = db_dates - csv_dates     # DB 有行但无导出（已知缺口现象）
    if reverse_gap:
        reporter.add("ERROR", f"历史一致性: {len(reverse_gap)} 个日期有导出文件但 DB 无对应行: "
                              f"{sorted(reverse_gap)[:5]}{'…' if len(reverse_gap) > 5 else ''}")
    else:
        reporter.add("PASS", f"历史一致性: 导出 {len(csv_dates)} 个日期全部有 DB 数据（无反向缺口）")
    if forward_gap:
        reporter.add("WARNING", f"历史一致性: {len(forward_gap)} 个日期 DB 有数据但无每日导出: "
                                f"{sorted(forward_gap)[-5:]}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="门户数据一致性只读校验（只 read/compare/report，绝不修改数据）")
    ap.add_argument("--db", default=str(ROOT / "data" / "database" / "smm_lithium.db"),
                    help="SQLite 数据库路径（只读打开）")
    ap.add_argument("--exports-dir", default=str(ROOT / "data" / "exports"),
                    help="导出目录（只读扫描）")
    ap.add_argument("--portal-config", default=None,
                    help="门户配置 YAML（默认生产配置；测试时指向合成配置）")
    ap.add_argument("--as-of", default=None,
                    help="榜单基准日 YYYY-MM-DD（默认服务器当天；次日采集模式下当天数据视为未发布）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 结果")
    args = ap.parse_args()

    fs.DB_PATH = Path(args.db)
    fs.EXPORTS_ROOT = Path(args.exports_dir)
    if args.portal_config:
        fs.PORTAL_CFG = Path(args.portal_config)
        fs._portal_cfg_cache.update({"mtime": 0.0, "data": {}})
    from datetime import datetime as _dt
    as_of = args.as_of or _dt.now().strftime("%Y-%m-%d")

    reporter = Reporter(args.db)
    try:
        con = _db_ro(args.db)
    except sqlite3.Error as e:
        reporter.add("ERROR", f"数据库无法只读打开: {e}")
    else:
        with con:
            check_metrics(reporter, con)
            check_rankings(reporter, con, as_of)
            check_exports(reporter, con, fs.EXPORTS_ROOT)
            check_history(reporter, con, fs.EXPORTS_ROOT)

    if args.json:
        print(reporter.to_json())
    else:
        reporter.print_report()
    return reporter.exit_code()


if __name__ == "__main__":
    sys.exit(main())
