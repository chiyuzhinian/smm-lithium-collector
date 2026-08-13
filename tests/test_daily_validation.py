"""每日统一数据验证层测试：合成数据构造 9 层场景，断言 PASS/WARNING/FAIL。

全部使用 tmp_path，不触碰正式数据目录。
"""
import csv
import json
import sqlite3
from pathlib import Path

import pandas as pd

from smm_collector.daily_validation import run_daily_validation
from smm_collector.exporter import COLUMNS, build_summary_dataframe

TARGET = "2026-08-13"
CANONICAL = ["锂化合物", "锂矿", "钴金属"]


def base_row(**kw):
    r = {"source": "SMM", "market": "SMM锂电现货", "category": "锂化合物",
         "product_name": "碳酸锂", "specification": "电池级", "min_price": "60000",
         "max_price": "70000", "average_price": "65000", "change_value": "0",
         "unit": "元/吨", "price_date": TARGET, "collected_at": "2026-08-13 10:00:00",
         "source_url": "https://x", "collection_method": "DOM", "raw_text": "",
         "extra_fields": "{}", "record_hash": "h1", "validation_status": "valid",
         "validation_message": ""}
    r.update(kw)
    return r


def _write_db(db_path: Path, rows: list[dict]):
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE lithium_spot_prices (
      id INTEGER PRIMARY KEY, source TEXT, market TEXT, category TEXT, product_name TEXT,
      specification TEXT, min_price TEXT, max_price TEXT, average_price TEXT, change_value TEXT,
      unit TEXT, price_date TEXT, collected_at TEXT, source_url TEXT, collection_method TEXT,
      raw_text TEXT, extra_fields TEXT, record_hash TEXT, validation_status TEXT,
      validation_message TEXT, created_at TEXT, updated_at TEXT)""")
    for r in rows:
        con.execute("INSERT INTO lithium_spot_prices (source,market,category,product_name,"
                    "specification,min_price,max_price,average_price,change_value,unit,price_date,"
                    "collected_at,source_url,collection_method,raw_text,extra_fields,record_hash,"
                    "validation_status,validation_message,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r.get("source"), r.get("market"), r.get("category"), r.get("product_name"),
                     r.get("specification") or "", r.get("min_price"), r.get("max_price"),
                     r.get("average_price"), r.get("change_value"), r.get("unit"),
                     r.get("price_date"), r.get("collected_at"), r.get("source_url"),
                     r.get("collection_method"), r.get("raw_text"), r.get("extra_fields"),
                     r.get("record_hash"), r.get("validation_status"), r.get("validation_message"),
                     "2026-08-13 10:00:00", "2026-08-13 10:00:00"))
    con.commit()
    con.close()


def build_env(tmp_path, rows=None, db_rows=None, with_fixed=None, xlsx_empty=False,
              csv_columns=None, make_xlsx=True, make_csv=True, make_manifest=True,
              make_db=True, canonical=None):
    rows = rows or []
    export_root = tmp_path / "exports"
    ymd = export_root / "2026" / "08" / "每日汇总"
    (ymd / "Excel").mkdir(parents=True, exist_ok=True)
    (ymd / "CSV").mkdir(parents=True, exist_ok=True)
    if make_xlsx:
        xlsx = ymd / "Excel" / f"SMM锂电现货价格_{TARGET}.xlsx"
        if xlsx_empty:
            pd.DataFrame(columns=COLUMNS).to_excel(xlsx, index=False, sheet_name="全部数据")
        else:
            pd.DataFrame(rows, columns=COLUMNS).to_excel(xlsx, index=False, sheet_name="全部数据")
    if make_csv:
        csvp = ymd / "CSV" / f"SMM锂电现货价格_{TARGET}.csv"
        cols = csv_columns if csv_columns is not None else COLUMNS
        with csvp.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols})
    if make_manifest:
        (ymd / f"SMM数据状态_{TARGET}.json").write_text(
            json.dumps({"generated_at": "2026-08-13T10:00:00", "target_date": TARGET},
                       ensure_ascii=False), encoding="utf-8")
    if with_fixed is not None:
        fdir = export_root / "固定汇总"
        fdir.mkdir(parents=True, exist_ok=True)
        fp = fdir / "SMM锂电现货价格_固定汇总.xlsx"
        pd.DataFrame({c: [None] * with_fixed for c in COLUMNS}).to_excel(
            fp, index=False, sheet_name="全部数据")
    db_path = tmp_path / "db" / "smm_lithium.db"
    if make_db:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _write_db(db_path, db_rows or [])
    portal = {"canonical_categories": canonical or CANONICAL,
              "summary_gate": {"min_date_alignment_ratio": 0.6, "max_invalid_ratio": 0.05}}
    return export_root, db_path, portal


def run(tmp_path, export_root, db_path, portal):
    vcfg = {"log_dir": str(tmp_path / "logs" / "validation")}
    return run_daily_validation(TARGET, db_path=db_path, export_root=export_root,
                                portal_cfg=portal, vcfg=vcfg)


# ── 全层通过 → PASS ─────────────────────────────────────────────

def test_pass_all_layers(tmp_path):
    today = [base_row()]
    prev = [base_row(price_date="2026-08-12", average_price="64000",
                     record_hash="h_prev", collected_at="2026-08-12 10:00:00")]
    export_root, db_path, portal = build_env(
        tmp_path, rows=today, db_rows=today + prev,
        with_fixed=len(build_summary_dataframe(today + prev)),
        canonical=["锂化合物"])
    res = run(tmp_path, export_root, db_path, portal)
    assert res["verdict"] == "PASS", res["issues"]
    assert (tmp_path / "logs" / "validation" / f"validation_{TARGET}.json").exists()


# ── 第 1 层：文件级 ─────────────────────────────────────────────

def test_missing_daily_xlsx_is_fail(tmp_path):
    export_root, db_path, portal = build_env(tmp_path, rows=[base_row()], make_xlsx=False,
                                             make_db=False, canonical=["锂化合物"])
    res = run(tmp_path, export_root, db_path, portal)
    assert res["verdict"] == "FAIL"
    assert any(i["layer"] == "file" and i["level"] == "error" for i in res["issues"])


def test_empty_daily_xlsx_is_fail(tmp_path):
    export_root, db_path, portal = build_env(tmp_path, rows=[base_row()], xlsx_empty=True,
                                             make_db=False, canonical=["锂化合物"])
    res = run(tmp_path, export_root, db_path, portal)
    assert res["verdict"] == "FAIL"
    assert any(i["layer"] == "file" and i["level"] == "error" for i in res["issues"])


# ── 第 2 层：结构级 ─────────────────────────────────────────────

def test_missing_core_column_is_fail(tmp_path):
    cols = [c for c in COLUMNS if c != "category"]
    export_root, db_path, portal = build_env(tmp_path, rows=[base_row()],
                                             csv_columns=cols, make_db=False,
                                             canonical=["锂化合物"])
    res = run(tmp_path, export_root, db_path, portal)
    assert res["verdict"] == "FAIL"
    assert any(i["layer"] == "schema" and i["level"] == "error" for i in res["issues"])


# ── 第 3 层：日期级（业务日期 ≠ 文件时间） ──────────────────────

def test_wrong_business_date_is_fail(tmp_path):
    rows = [base_row(product_name=f"产品{i}", price_date="2026-07-31", record_hash=f"h{i}")
            for i in range(20)]
    export_root, db_path, portal = build_env(tmp_path, rows=rows, make_db=False,
                                             canonical=["锂化合物"])
    res = run(tmp_path, export_root, db_path, portal)
    assert res["verdict"] == "FAIL"
    assert any(i["layer"] == "date" and i["level"] == "error" for i in res["issues"])


def test_low_alignment_is_warning(tmp_path):
    rows = [base_row(product_name=f"产品{i}", price_date="2026-08-12", record_hash=f"h{i}")
            for i in range(10)]
    rows += [base_row(product_name="今日品", record_hash="today")]  # 仅 1/11 对齐
    export_root, db_path, portal = build_env(tmp_path, rows=rows, make_db=False,
                                             canonical=["锂化合物"])
    res = run(tmp_path, export_root, db_path, portal)
    assert res["verdict"] in ("WARNING", "FAIL")
    assert any(i["layer"] == "date" for i in res["issues"])


# ── 第 4 层：重复级 ─────────────────────────────────────────────

def test_duplicate_same_price_only_info(tmp_path):
    rows = [base_row(), base_row(record_hash="h1")]  # 同键同价
    export_root, db_path, portal = build_env(tmp_path, rows=rows, with_fixed=1,
                                             make_db=False, canonical=["锂化合物"])
    res = run(tmp_path, export_root, db_path, portal)
    assert res["verdict"] == "PASS"
    assert res["layers"]["dedup"]["duplicate_rows"] == 1
    assert any(i["layer"] == "dedup" and i["level"] == "info" for i in res["issues"])


def test_conflict_same_key_different_price_is_warning(tmp_path):
    rows = [base_row(), base_row(average_price="99999", record_hash="h1")]
    export_root, db_path, portal = build_env(tmp_path, rows=rows, make_db=False,
                                             canonical=["锂化合物"])
    res = run(tmp_path, export_root, db_path, portal)
    assert any(i["layer"] == "dedup" and i["level"] == "warning"
               and "冲突" in i["message"] for i in res["issues"])
    assert res["layers"]["dedup"]["conflict_count"] == 1


# ── 第 5 层：缺失分类级 ─────────────────────────────────────────

def test_missing_categories_is_warning(tmp_path):
    export_root, db_path, portal = build_env(tmp_path, rows=[base_row()], make_db=False,
                                             canonical=["锂化合物", "锂矿"])
    res = run(tmp_path, export_root, db_path, portal)
    assert res["layers"]["categories"]["status"] == "warn"
    assert res["layers"]["categories"]["missing_categories"] == ["锂矿"]
    assert res["layers"]["categories"]["expected_count"] == 2
    assert res["layers"]["categories"]["success_count"] == 1


# ── 第 6 层：数值级 ─────────────────────────────────────────────

def test_all_invalid_rows_is_fail(tmp_path):
    rows = [base_row(validation_status="invalid", validation_message="最低价大于最高价",
                     min_price="70000", max_price="60000")]
    export_root, db_path, portal = build_env(tmp_path, rows=rows, make_db=False,
                                             canonical=["锂化合物"])
    res = run(tmp_path, export_root, db_path, portal)
    assert res["verdict"] == "FAIL"
    assert any(i["layer"] == "numeric" and i["level"] == "error" for i in res["issues"])


def test_low_gt_high_single_row_is_warning(tmp_path):
    rows = [base_row(), base_row(product_name="坏行", record_hash="bad",
                                 validation_status="invalid",
                                 validation_message="最低价大于最高价",
                                 min_price="70000", max_price="60000")]
    export_root, db_path, portal = build_env(tmp_path, rows=rows, make_db=False,
                                             canonical=["锂化合物"])
    res = run(tmp_path, export_root, db_path, portal)
    assert res["verdict"] == "WARNING"
    assert res["layers"]["numeric"]["numeric_problems"].get("最低价大于最高价") == 1


# ── 第 7 层：异常波动级（只告警，不拒绝数据） ───────────────────

def test_volatility_spike_is_warning_not_fail(tmp_path):
    today = [base_row(average_price="700000")]
    prev = [base_row(price_date="2026-08-12", average_price="70000",
                     record_hash="h_prev", collected_at="2026-08-12 10:00:00")]
    export_root, db_path, portal = build_env(
        tmp_path, rows=today, db_rows=today + prev,
        with_fixed=len(build_summary_dataframe(today + prev)),
        canonical=["锂化合物"])
    res = run(tmp_path, export_root, db_path, portal)
    assert res["verdict"] == "WARNING"
    vol = res["layers"]["volatility"]
    assert vol["status"] == "warn" and len(vol["hits"]) == 1
    assert vol["hits"][0]["pct_change"] is not None and vol["hits"][0]["pct_change"] > 1.0


def test_volatility_threshold_configurable(tmp_path):
    today = [base_row(average_price="65000")]  # 与昨日 64000 差 1.6%
    prev = [base_row(price_date="2026-08-12", average_price="64000",
                     record_hash="h_prev", collected_at="2026-08-12 10:00:00")]
    export_root, db_path, portal = build_env(
        tmp_path, rows=today, db_rows=today + prev,
        with_fixed=len(build_summary_dataframe(today + prev)),
        canonical=["锂化合物"])
    vcfg = {"log_dir": str(tmp_path / "logs" / "validation"),
            "volatility_warning_pct": 1.0}  # 阈值收到 1% → 1.6% 触发
    res = run_daily_validation(TARGET, db_path=db_path, export_root=export_root,
                               portal_cfg=portal, vcfg=vcfg)
    assert res["layers"]["volatility"]["hits"], "1% 阈值下 1.6% 波动应命中"


# ── 第 8 层：历史连续性级（仅提示） ─────────────────────────────

def test_continuity_gap_is_warning(tmp_path):
    a_prev = base_row(price_date="2026-08-11", record_hash="a11",
                      collected_at="2026-08-11 10:00:00")
    a_today = base_row(record_hash="a13")  # A 序列缺 08-12
    b_mid = base_row(category="锂矿", product_name="锂辉石", record_hash="b12",
                     price_date="2026-08-12", collected_at="2026-08-12 10:00:00")
    b_today = base_row(category="锂矿", product_name="锂辉石", record_hash="b13")
    db_rows = [a_prev, a_today, b_mid, b_today]
    export_root, db_path, portal = build_env(
        tmp_path, rows=[a_today, b_today], db_rows=db_rows, make_db=True,
        canonical=["锂化合物", "锂矿"])
    res = run(tmp_path, export_root, db_path, portal)
    cont = res["layers"]["continuity"]
    assert cont["status"] == "warn" and cont["gap_series"] >= 1
    assert any("2026-08-12" in g["missing_price_dates"] for g in cont["gaps"])


# ── 第 9 层：固定汇总一致性 ─────────────────────────────────────

def test_fixed_summary_inconsistent_is_warning(tmp_path):
    today = [base_row()]
    prev = [base_row(price_date="2026-08-12", record_hash="h_prev",
                     collected_at="2026-08-12 10:00:00")]
    export_root, db_path, portal = build_env(
        tmp_path, rows=today, db_rows=today + prev,
        with_fixed=1,  # 期望 2 行，正式文件只有 1 行 → 不一致
        canonical=["锂化合物"])
    res = run(tmp_path, export_root, db_path, portal)
    assert res["layers"]["fixed_summary"]["status"] == "warn"
    assert res["layers"]["fixed_summary"]["expected_rows"] == 2
    assert any(i["layer"] == "fixed_summary" and "重建" in i["message"] for i in res["issues"])
