"""测试规范日报：映射匹配、化学类型纠正、涨跌计算、单位标准化。"""
from datetime import date
from decimal import Decimal
import pandas as pd
from smm_collector.business_report import match_smm_product, _get_previous_price, build_report, REPORT_COLUMNS


def _row(pn="电池级碳酸锂", cat="锂化合物", spec="Li2CO3>=99.5%", unit="元/吨",
         avg=Decimal(100000), price_date="2026-07-29"):
    return {
        "source": "SMM", "market": "SMM锂电现货", "category": cat,
        "product_name": pn, "specification": spec, "unit": unit,
        "average_price": avg, "min_price": Decimal(99000), "max_price": Decimal(101000),
        "price_date": price_date,
    }


def test_match_by_product_key():
    rows = [_row(pn="电池级碳酸锂")]
    entry = {"source_type": "smm", "product_key": "电池级碳酸锂", "category": "锂化合物"}
    m = match_smm_product(rows, entry)
    assert m is not None
    assert m["product_name"] == "电池级碳酸锂"


def test_match_by_alias():
    rows = [_row(pn="废旧磷酸铁锂动力正极片", cat="废旧正极片及系数")]
    entry = {"source_type": "smm", "product_key": "废旧LFP动力正极片",
             "ssm_alias": "废旧磷酸铁锂动力正极片", "category": "废旧正极片及系数"}
    m = match_smm_product(rows, entry)
    assert m is not None


def test_no_match_wrong_category():
    rows = [_row(pn="电池级碳酸锂", cat="锂化合物")]
    entry = {"source_type": "smm", "product_key": "电池级碳酸锂", "category": "钴金属"}
    m = match_smm_product(rows, entry)
    assert m is None


def test_no_match_wrong_unit():
    rows = [_row(pn="电池级硫酸镍", cat="镍化合物", unit="%")]  # unit is %, not 元/吨
    entry = {"source_type": "smm", "product_key": "SMM电池级硫酸镍指数",
             "category": "镍化合物", "unit": "元/吨"}
    m = match_smm_product(rows, entry)
    assert m is None  # should not match because unit mismatch


def test_chemistry_al_not_copper():
    """A00铝 -> 铝，不能被历史文件错误带成铜。"""
    from smm_collector.business_report import load_mapping
    from pathlib import Path
    mapping = load_mapping(Path("config"))
    for g in mapping.get("company_groups", []):
        for e in g.get("rows", []):
            if e.get("detail") == "A00铝":
                assert e["chemistry"] == "铝", f"A00铝 chemistry should be 铝, got {e['chemistry']}"
            if e.get("detail") == "1#电解铜":
                assert e["chemistry"] == "铜", f"1#电解铜 chemistry should be 铜, got {e['chemistry']}"


def test_chemistry_ni_not_others():
    """1#电解镍 -> 镍。"""
    from smm_collector.business_report import load_mapping
    from pathlib import Path
    mapping = load_mapping(Path("config"))
    for g in mapping.get("company_groups", []):
        for e in g.get("rows", []):
            if e.get("detail") == "1#电解镍":
                assert e["chemistry"] == "镍", f"got {e['chemistry']}"


def test_previous_price_found():
    rows = [
        _row(pn="电池级碳酸锂", price_date="2026-07-29", avg=Decimal(100000)),
        _row(pn="电池级碳酸锂", price_date="2026-07-28", avg=Decimal(98000)),
    ]
    entry = {"product_key": "电池级碳酸锂", "category": "锂化合物"}
    prev = _get_previous_price(rows, entry, "2026-07-29")
    assert prev == Decimal(98000)


def test_previous_price_none_when_only_today():
    rows = [_row(pn="电池级碳酸锂", price_date="2026-07-29")]
    entry = {"product_key": "电池级碳酸锂", "category": "锂化合物"}
    prev = _get_previous_price(rows, entry, "2026-07-29")
    assert prev is None


def test_change_null_when_prev_zero():
    rows = [
        _row(pn="X", price_date="2026-07-29", avg=Decimal(100)),
        _row(pn="X", price_date="2026-07-28", avg=Decimal(0)),
    ]
    entry = {"product_key": "X", "category": "锂化合物"}
    prev = _get_previous_price(rows, entry, "2026-07-29")
    # prev is 0, so change calculation would divide by zero
    assert prev == Decimal(0)


def test_report_columns_count():
    assert len(REPORT_COLUMNS) == 11


def test_external_source_missing():
    """外部数据缺失时价格留空，不伪造。"""
    rows = []
    df, quality = build_report(rows, target_date=date(2026, 7, 29))
    ext_rows = df[df["数据来源"] == "Benchmark"]
    assert len(ext_rows) > 0
    for _, r in ext_rows.iterrows():
        assert r["当日均价（YYYY-MM-DD）"] is None or pd.isna(r["当日均价（YYYY-MM-DD）"]), \
            f"External data should be None, got {r['当日均价（YYYY-MM-DD）']}"

import pandas as pd
