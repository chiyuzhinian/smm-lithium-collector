"""测试近N日价格统计：分组、日期选择、均价计算。"""
from datetime import date
from decimal import Decimal
from smm_collector.price_statistics import (
    product_key, group_by_product,
    compute_rolling_average, enrich_with_rolling_average,
    select_window_dates, PRODUCT_KEY_FIELDS,
)


def _row(category="锂矿", product_name="锂辉石", spec="Li2O>=6%", unit="元/吨",
         price_date="2026-07-22", avg=Decimal(5500), status="valid", source="SMM",
         market="SMM锂电现货"):
    return {
        "source": source, "market": market, "category": category,
        "product_name": product_name, "specification": spec, "unit": unit,
        "price_date": price_date, "average_price": avg,
        "validation_status": status, "min_price": Decimal(5000),
        "max_price": Decimal(6000),
    }


# ── 日期窗口 ──

def test_select_window_3_of_5():
    dates = ["2026-07-17", "2026-07-18", "2026-07-21", "2026-07-22", "2026-07-23"]
    result = select_window_dates(dates, 3)
    assert result == ["2026-07-21", "2026-07-22", "2026-07-23"]

def test_select_window_2_of_2():
    dates = ["2026-07-22", "2026-07-23"]
    result = select_window_dates(dates, 3)
    assert result == ["2026-07-22", "2026-07-23"]

def test_select_window_1():
    dates = ["2026-07-23"]
    result = select_window_dates(dates, 3)
    assert result == ["2026-07-23"]


# ── 产品分组 ──

def test_product_key_same():
    r1 = _row(); r2 = _row()
    assert product_key(r1) == product_key(r2)

def test_product_key_different_spec():
    r1 = _row(spec="A"); r2 = _row(spec="B")
    assert product_key(r1) != product_key(r2)

def test_product_key_different_unit():
    r1 = _row(unit="元/吨"); r2 = _row(unit="美元/千克")
    assert product_key(r1) != product_key(r2)

def test_product_key_different_category():
    r1 = _row(category="锂矿"); r2 = _row(category="锂金属")
    assert product_key(r1) != product_key(r2)

def test_group_by_product_mixed():
    rows = [_row(product_name="A"), _row(product_name="B"), _row(product_name="A")]
    groups = group_by_product(rows)
    assert len(groups) == 2


# ── 均价计算 ──

def test_full_three_day_average():
    rows = [
        _row(price_date="2026-07-21", avg=Decimal(80000)),
        _row(price_date="2026-07-22", avg=Decimal(81000)),
        _row(price_date="2026-07-23", avg=Decimal(82000)),
    ]
    avg, count = compute_rolling_average(rows, ["2026-07-21", "2026-07-22", "2026-07-23"])
    assert avg == Decimal(81000)
    assert count == 3

def test_two_day_average():
    rows = [
        _row(price_date="2026-07-22", avg=Decimal(80000)),
        _row(price_date="2026-07-23", avg=Decimal(82000)),
    ]
    avg, count = compute_rolling_average(rows, ["2026-07-22", "2026-07-23"])
    assert avg == Decimal(81000)
    assert count == 2

def test_one_day_average():
    rows = [_row(price_date="2026-07-23", avg=Decimal(82000))]
    avg, count = compute_rolling_average(rows, ["2026-07-23"])
    assert avg == Decimal(82000)
    assert count == 1

def test_all_empty_prices():
    rows = [_row(price_date="2026-07-23", avg=None)]
    avg, count = compute_rolling_average(rows, ["2026-07-23"])
    assert avg is None
    assert count == 0

def test_exclude_invalid():
    rows = [
        _row(price_date="2026-07-22", avg=Decimal(80000), status="valid"),
        _row(price_date="2026-07-23", avg=Decimal(100), status="invalid"),
    ]
    avg, count = compute_rolling_average(rows, ["2026-07-22", "2026-07-23"], exclude_invalid=True)
    assert avg == Decimal(80000)
    assert count == 1

def test_include_warning():
    rows = [
        _row(price_date="2026-07-22", avg=Decimal(80000), status="valid"),
        _row(price_date="2026-07-23", avg=Decimal(81000), status="warning"),
    ]
    avg, count = compute_rolling_average(rows, ["2026-07-22", "2026-07-23"], include_warning=True)
    assert avg == Decimal(80500)
    assert count == 2

def test_decimal_precision():
    """Decimal 精度不丢失。"""
    rows = [
        _row(price_date="2026-07-21", avg=Decimal("80000.5")),
        _row(price_date="2026-07-22", avg=Decimal("81000.5")),
        _row(price_date="2026-07-23", avg=Decimal("82000.5")),
    ]
    avg, count = compute_rolling_average(rows, ["2026-07-21", "2026-07-22", "2026-07-23"])
    assert avg == Decimal("81000.5")
    assert count == 3


# ── 批量增强 ──

def test_enrich_same_product_same_average():
    """同一产品在窗口内所有行的三日均价一致。"""
    rows = [
        _row(product_name="X", price_date="2026-07-21", avg=Decimal(100)),
        _row(product_name="X", price_date="2026-07-22", avg=Decimal(200)),
        _row(product_name="X", price_date="2026-07-23", avg=Decimal(300)),
    ]
    enriched = enrich_with_rolling_average(rows, ["2026-07-21", "2026-07-22", "2026-07-23"])
    avgs = [r["three_day_average_price"] for r in enriched]
    assert all(a == Decimal(200) for a in avgs)
    counts = [r.get("three_day_valid_count", 0) for r in enriched]
    assert all(c == 3 for c in counts)

def test_enrich_different_products_separate():
    """不同产品的均价不应混合。"""
    rows = [
        _row(product_name="A", price_date="2026-07-23", avg=Decimal(100)),
        _row(product_name="B", price_date="2026-07-23", avg=Decimal(500)),
    ]
    enriched = enrich_with_rolling_average(rows, ["2026-07-23"])
    a_row = [r for r in enriched if r["product_name"] == "A"][0]
    b_row = [r for r in enriched if r["product_name"] == "B"][0]
    assert a_row["three_day_average_price"] == Decimal(100)
    assert b_row["three_day_average_price"] == Decimal(500)
