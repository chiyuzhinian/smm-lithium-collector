#!/usr/bin/env python3
"""重点产品价格模块只读一致性校验（DB ↔ 门户 API 对账）。

用途：核验首页「重点产品价格」的 /api/key-products 与 /api/key-products/history
返回的每一个价格、日期、涨跌都来自数据库真实记录：
  - 最新价 / 数据日期 / 单位 == DB 独立重算合并序列末点
  - 较上次（prev_date / change_pct）== DB 相邻真实记录计算（含 08-03 规格改名对）
  - 7 天 spark_points == 最新数据日期往前 7 个自然日窗口内 DB 真实点（断言无补点）
  - history 7d/30d == 同窗口过滤结果逐点相等（30d 必须包含改名前的旧规格点）
  - API 每个点都能在 DB 参考集合中找到（反向包含，杜绝 invalid/伪造点混入）

原则：只读（DB 以 mode=ro 打开）、只比较、只报告——绝不修改任何数据。

退出码：0=全部 PASS；1=存在 ERROR；2=仅存在 WARNING（无 ERROR）。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import file_server as fs  # noqa: E402  门户模块（import 安全，服务启动在 __main__ 守卫内）
from verify_data_consistency import Reporter, _close, _db_ro  # noqa: E402

TOL = 1e-6
DATE_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"

# 必检样本（任务指定全部产品 + 高端款，共 10 个）
SAMPLE_KEYS = ["kp_lce_bat", "kp_niso4", "kp_waste_dl", "kp_lfp_jpf",
               "kp_ncm811", "kp_ncm523", "kp_lfp_255", "kp_lfp_230",
               "kp_lfp_xf", "kp_lfp_xf_h"]


def ref_series(con: sqlite3.Connection) -> dict:
    """独立参考实现：每 (category, product_name, specification, 日期) 取
    collected_at 最新（同刻 id 大者）的合法行，按 key 合并三元组为升序序列。

    与门户 _key_product_rows 的 ORDER BY + 首见即留 逻辑分开实现（窗口函数），
    用于互相对账。
    """
    defs = fs._key_product_defs()
    triples = [(t["category"], t["product_name"], t["specification"])
               for e in defs for t in e["db"]]
    if not triples:
        return {}
    conds = " OR ".join("(category = ? AND product_name = ? AND specification = ?)"
                        for _ in triples)
    params: tuple = tuple(v for t in triples for v in t)
    rows = con.execute(
        f"""WITH ranked AS (
              SELECT category, product_name, specification, unit, price_date, average_price,
                     ROW_NUMBER() OVER (
                       PARTITION BY price_date, category, product_name, specification
                       ORDER BY collected_at DESC, id DESC) AS rn
              FROM lithium_spot_prices
              WHERE validation_status != 'invalid'
                AND price_date GLOB '{DATE_GLOB}'
                AND ({conds}))
            SELECT category, product_name, specification, unit, price_date, average_price
            FROM ranked WHERE rn = 1 AND average_price IS NOT NULL
            ORDER BY price_date""", params).fetchall()
    by_triple: dict = {}
    for r in rows:
        try:
            val = float(r["average_price"])
        except (TypeError, ValueError):
            continue
        key = (r["category"], r["product_name"], r["specification"] or "")
        by_triple.setdefault(key, []).append(
            {"date": str(r["price_date"]), "value": val, "unit": str(r["unit"] or "")})
    out: dict = {}
    for e in defs:
        pts = []
        for t in e["db"]:
            pts.extend(by_triple.get((t["category"], t["product_name"], t["specification"] or ""), []))
        pts.sort(key=lambda p: p["date"])
        out[e["key"]] = pts
    return out


def expected_card(pts: list[dict]) -> dict:
    """由参考序列按 API 口径重算卡片字段（round 4 / round 2 / 7 自然日窗口）。"""
    card = {"value": None, "unit": "", "change": None, "change_pct": None,
            "prev_date": None, "price_date": None, "spark_points": []}
    if not pts:
        return card
    last, prev = pts[-1], (pts[-2] if len(pts) >= 2 else None)
    card["value"] = round(last["value"], 4)
    card["unit"] = last["unit"] or (prev["unit"] if prev else "")
    card["price_date"] = last["date"]
    spark_from = (datetime.strptime(last["date"], "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")
    card["spark_points"] = [{"date": p["date"], "value": round(p["value"], 4)}
                            for p in pts if p["date"] >= spark_from]
    if prev is not None:
        card["prev_date"] = prev["date"]
        card["change"] = round(card["value"] - prev["value"], 4)
        if prev["value"]:
            card["change_pct"] = round(card["change"] / prev["value"] * 100, 2)
    return card


def expected_history(pts: list[dict], days: int) -> tuple[str | None, str | None, list[dict]]:
    """参考窗口：最新数据日期往前 days-1 天内的真实点。"""
    if not pts:
        return None, None, []
    latest = pts[-1]["date"]
    date_from = (datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    data = [{"date": p["date"], "price": round(p["value"], 4)}
            for p in pts if p["date"] >= date_from]
    return latest, date_from, data


def check_payload(reporter: Reporter, con: sqlite3.Connection) -> dict:
    """检查1：卡片数量/key 唯一/全部 20 卡点集合 ⊆ DB 参考集合。"""
    payload = fs._key_products_payload()
    products = payload.get("products") or []
    ref = ref_series(con)
    if len(products) != 20:
        reporter.add("ERROR", f"产品卡数量: {len(products)} != 20（配置缺失或重复）")
    else:
        reporter.add("PASS", "产品卡数量: 20 张与配置一致")
    keys = [p["key"] for p in products]
    if len(keys) != len(set(keys)):
        reporter.add("ERROR", "产品卡 key 唯一性: 存在重复 key（磷化工双收录嫌疑）")
    else:
        reporter.add("PASS", "产品卡 key 唯一性: 20 个 key 全部唯一")
    # 全部卡（含非样本）的点必须都能在 DB 参考中找到
    bad = 0
    for p in products:
        rp = ref.get(p["key"], [])
        ref_set = {(x["date"], round(x["value"], 4)) for x in rp}
        for s in p.get("spark_points") or []:
            if (s["date"], round(s["value"], 4)) not in ref_set:
                reporter.add("ERROR", f"反向包含: {p['key']} spark 点 {s} 不在 DB 参考集合中")
                bad += 1
    if bad == 0:
        reporter.add("PASS", "反向包含: 全部产品卡的每个点都能在 DB 参考集合中找到")
    return ref


def check_samples(reporter: Reporter, ref: dict) -> None:
    """检查2/3/4/5：10 个必检样本的最新价/较上次/7天窗口/history 7d+30d。"""
    payload = fs._key_products_payload()
    cards = {p["key"]: p for p in payload.get("products") or []}
    for key in SAMPLE_KEYS:
        card = cards.get(key)
        rp = ref.get(key)
        if card is None or rp is None:
            reporter.add("ERROR", f"样本 {key}: 卡片或参考序列缺失")
            continue
        exp = expected_card(rp)
        # 最新价 / 日期 / 单位
        if card["value"] is None and exp["value"] is None:
            reporter.add("PASS", f"样本 {key}: 无数据（API 与 DB 一致为暂无）")
            continue
        if not _close(card["value"], exp["value"]) or card["price_date"] != exp["price_date"]:
            reporter.add("ERROR", f"样本 {key}: 最新价 API={card['value']}@{card['price_date']} "
                                  f"DB={exp['value']}@{exp['price_date']}")
        else:
            reporter.add("PASS", f"样本 {key}: 最新价 {card['value']} {card['unit']} "
                                 f"@ {card['price_date']} 与 DB 一致")
        if card["unit"] != exp["unit"]:
            reporter.add("ERROR", f"样本 {key}: 单位 API={card['unit']!r} DB={exp['unit']!r}")
        # 较上次（含跨改名对：08-03 的 prev 必须是 07-31 旧规格值）
        if exp["change_pct"] is None:
            if card["change_pct"] is not None or card["prev_date"] is not None:
                reporter.add("ERROR", f"样本 {key}: DB 仅 1 个数据日期，API 却返回了较上次")
            else:
                reporter.add("PASS", f"样本 {key}: 仅 1 条历史记录，较上次为空（一致）")
        else:
            if card["prev_date"] != exp["prev_date"] or not _close(card["change_pct"], exp["change_pct"]):
                reporter.add("ERROR", f"样本 {key}: 较上次 API={card['change_pct']}%"
                                      f"(prev {card['prev_date']}) DB={exp['change_pct']}%"
                                      f"(prev {exp['prev_date']})")
            else:
                reporter.add("PASS", f"样本 {key}: 较上次 ({exp['prev_date']}) "
                                     f"{exp['change_pct']}% 与 DB 一致")
        # 7 天 spark（断言逐点相等 → 证明无补点/无填充）
        got_spark = card.get("spark_points") or []
        if [ (s["date"], round(s["value"], 4)) for s in got_spark ] != \
           [ (s["date"], round(s["value"], 4)) for s in exp["spark_points"] ]:
            reporter.add("ERROR", f"样本 {key}: 7天spark 与 DB 窗口不一致 API={got_spark} "
                                  f"DB={exp['spark_points']}")
        else:
            reporter.add("PASS", f"样本 {key}: 7天spark {len(got_spark)} 个真实点与 DB 窗口一致（无补点）")
        # history 7d / 30d
        for range_str, days in (("7d", 7), ("30d", 30)):
            got = fs._key_product_history_payload(key, range_str)
            latest, date_from, exp_data = expected_history(rp, days)
            got_data = got.get("data") or []
            if got.get("latest_date") != latest:
                reporter.add("ERROR", f"样本 {key} {range_str}: latest_date API={got.get('latest_date')} "
                                      f"DB={latest}")
            if [(d["date"], round(d["price"], 4)) for d in got_data] != \
               [(d["date"], round(d["price"], 4)) for d in exp_data]:
                reporter.add("ERROR", f"样本 {key} {range_str}: 数据点与 DB 窗口不一致 "
                                      f"API n={len(got_data)} DB n={len(exp_data)}")
            else:
                reporter.add("PASS", f"样本 {key} {range_str}: {len(got_data)} 个真实点与 DB 窗口一致"
                                     f"（{date_from} 起，含改名合并）")
            if got.get("unit") != (rp[-1]["unit"] if rp else ""):
                reporter.add("ERROR", f"样本 {key} {range_str}: 单位 API={got.get('unit')!r} "
                                      f"DB={rp[-1]['unit']!r}")


def check_unknown_key(reporter: Reporter) -> None:
    """检查6：未知 key 必须返回 error，不得回退到任何数据。"""
    got = fs._key_product_history_payload("nope_not_exist", "30d")
    if got.get("error") and not (got.get("data") or []):
        reporter.add("PASS", "未知 key: 返回错误且无数据（不回退到其他产品）")
    else:
        reporter.add("ERROR", f"未知 key: 返回异常 {got}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="重点产品价格模块只读一致性校验（只 read/compare/report，绝不修改数据）")
    ap.add_argument("--db", default=str(ROOT / "data" / "database" / "smm_lithium.db"),
                    help="SQLite 数据库路径（只读打开）")
    ap.add_argument("--portal-config", default=None,
                    help="门户配置 YAML（默认生产配置；测试时指向合成配置）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 结果")
    args = ap.parse_args()

    fs.DB_PATH = Path(args.db)
    if args.portal_config:
        fs.PORTAL_CFG = Path(args.portal_config)
        fs._portal_cfg_cache.update({"mtime": 0.0, "data": {}})

    reporter = Reporter(args.db)
    try:
        con = _db_ro(args.db)
    except sqlite3.Error as e:
        reporter.add("ERROR", f"数据库无法只读打开: {e}")
    else:
        with con:
            ref = check_payload(reporter, con)
            check_samples(reporter, ref)
            check_unknown_key(reporter)

    if args.json:
        print(reporter.to_json())
    else:
        reporter.print_report()
    return reporter.exit_code()


if __name__ == "__main__":
    sys.exit(main())
