"""页面数据日期校准（modal_price_date）及其下游联动测试。"""
from datetime import date
from decimal import Decimal

from smm_collector.validator import modal_price_date, validate_row
from smm_collector.daily_validation import _check_dates
from smm_collector.exporter import update_summaries


def _rows(dates):
    out = []
    for d in dates:
        out.append({"source": "SMM", "market": "SMM锂电现货", "category": "锂矿",
                    "product_name": "矿石", "price_date": d, "unit": "元/吨",
                    "min_price": Decimal(1), "max_price": Decimal(3),
                    "average_price": Decimal(2)})
    return out


# ── modal_price_date ─────────────────────────────────────────

def test_modal_empty_rows_falls_back():
    assert modal_price_date([], date(2026, 8, 17)) == date(2026, 8, 17)


def test_modal_ignores_non_date_types():
    rows = [{"price_date": "2026-08-14"}, {"price_date": None}]
    assert modal_price_date(rows, date(2026, 8, 17)) == date(2026, 8, 17)


def test_modal_picks_majority():
    rows = _rows([date(2026, 8, 14)] * 10 + [date(2026, 8, 13)] * 2)
    assert modal_price_date(rows, date(2026, 8, 17)) == date(2026, 8, 14)


def test_modal_future_date_falls_back():
    rows = _rows([date(2026, 8, 20)] * 5)
    assert modal_price_date(rows, date(2026, 8, 17)) == date(2026, 8, 17)


def test_modal_too_stale_falls_back():
    rows = _rows([date(2026, 7, 1)] * 5)
    assert modal_price_date(rows, date(2026, 8, 17)) == date(2026, 8, 17)


def test_modal_stale_boundary_allowed():
    # 恰好 14 天 → 仍按页面日期校准（不视为异常）
    rows = _rows([date(2026, 8, 3)] * 5)
    assert modal_price_date(rows, date(2026, 8, 17), max_stale_days=14) == date(2026, 8, 3)


# ── validate_row 重跑（校准后状态翻转）──────────────────────

def test_validate_row_rerun_with_calibrated_date():
    r = {"source": "SMM", "market": "SMM锂电现货", "category": "锂矿",
         "product_name": "矿石", "price_date": date(2026, 8, 14),
         "unit": "元/吨", "min_price": Decimal(1), "max_price": Decimal(3),
         "average_price": Decimal(2)}
    first = validate_row(r, date(2026, 8, 17))
    assert first["validation_status"] == "warning"
    assert "不是目标日期" in first["validation_message"]
    second = validate_row(r, date(2026, 8, 14))  # 校准后重跑
    assert second["validation_status"] == "valid"
    assert second["validation_message"] == ""


# ── daily_validation 日期层按数据日期校验 ───────────────────

def test_check_dates_aligned_to_data_date():
    rows = [{"price_date": "2026-08-14"} for _ in range(9)] + [{"price_date": "2026-08-13"}]
    issues = []
    out = _check_dates(rows, "2026-08-14", None, issues)  # effective=data_date
    assert out["alignment_ratio"] == 0.9
    assert out["status"] == "ok"
    assert issues == []


def test_check_dates_zero_match_still_fails():
    rows = [{"price_date": "2026-08-14"} for _ in range(20)]
    issues = []
    out = _check_dates(rows, "2026-08-17", None, issues)
    assert out["alignment_ratio"] == 0.0
    assert out["status"] == "fail"
    assert any("对齐率 0" in i["message"] for i in issues)


# ── 门控按数据日期对齐 ─────────────────────────────────────

def _valid_rows(n=10):
    rows = []
    for _ in range(n):
        r = {"source": "SMM", "market": "SMM锂电现货", "category": "锂化合物",
             "product_name": "电池级碳酸锂", "specification": "", "unit": "元/吨",
             "price_date": date(2026, 8, 14), "collected_at": "2026-08-17T10:00:00"}
        rows.append(validate_row(r, date(2026, 8, 14)))
    return rows


def test_update_summaries_gate_uses_data_date(tmp_path):
    rows = _valid_rows()
    meta = {"status": "success", "success_categories": ["锂化合物"]}
    decision = update_summaries(rows, meta, tmp_path, date(2026, 8, 14),
                                canonical_categories=["锂化合物"])
    assert decision["decision"] == "updated_formal"
    assert decision["eligible"] is True
    assert decision["align_ratio"] == 1.0
    assert decision["formal_status"] == "完整"
    assert (tmp_path / "固定汇总" / "SMM锂电现货价格_固定汇总.xlsx").exists()


def test_update_summaries_merge_dedups_across_excel_roundtrip(tmp_path):
    # 同一数据日期跑两次 updated_formal：Excel 往返后 price_date 变 datetime、
    # 空字符串变 NaN，与 date 对象/空串同值不同型；未归一化时去重失效导致
    # 行数膨胀（回归测试：第二次合并后行数必须与第一次一致）。
    import pandas as pd
    rows = _valid_rows()
    meta = {"status": "success", "success_categories": ["锂化合物"]}
    d1 = update_summaries(rows, meta, tmp_path, date(2026, 8, 14),
                          canonical_categories=["锂化合物"])
    n1 = len(pd.read_excel(tmp_path / "SMM锂电现货价格_历史汇总.xlsx"))
    d2 = update_summaries(rows, meta, tmp_path, date(2026, 8, 14),
                          canonical_categories=["锂化合物"])
    n2 = len(pd.read_excel(tmp_path / "SMM锂电现货价格_历史汇总.xlsx"))
    assert d1["decision"] == d2["decision"] == "updated_formal"
    assert n1 > 0 and n2 == n1  # 第二次合并不应新增重复行


def test_update_summaries_calendar_target_would_block(tmp_path):
    # 同一批数据按运行日（未校准）门控 → 对齐率 0 → 临时快照（旧行为，证明 data_date 的差异）
    rows = _valid_rows()
    meta = {"status": "success", "success_categories": ["锂化合物"]}
    decision = update_summaries(rows, meta, tmp_path, date(2026, 8, 17),
                                canonical_categories=["锂化合物"])
    assert decision["decision"] == "temp_snapshot"
    assert decision["eligible"] is False
    assert not (tmp_path / "固定汇总" / "SMM锂电现货价格_固定汇总.xlsx").exists()
