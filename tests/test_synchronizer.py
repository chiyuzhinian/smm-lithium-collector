"""测试同步逻辑（不依赖真实 MySQL）。"""
import hashlib
from datetime import date, datetime
from decimal import Decimal
from smm_collector.synchronizer import compute_record_hash, _classify_rows
from smm_collector.cleaner import normalize_business_key

def sample_row(**kw):
    r = {"source": "SMM", "market": "SMM锂电现货", "category": "锂矿",
         "product_name": "锂辉石", "specification": "Li₂O≥6%",
         "min_price": Decimal(5000), "max_price": Decimal(6000),
         "average_price": Decimal(5500), "change_value": Decimal(0),
         "unit": "元/吨", "price_date": date(2026, 7, 22),
         "collected_at": datetime(2026, 7, 23, 9, 0),
         "record_hash": "", "validation_status": "valid",
         "validation_message": "", "created_at": "2026-07-23T09:00:00",
         "updated_at": "2026-07-23T09:00:00"}
    r.update(kw)
    return r

def test_compute_hash_stable():
    r1 = sample_row()
    r1 = normalize_business_key(r1)
    h1 = compute_record_hash(r1)
    h2 = compute_record_hash(r1)
    assert h1 == h2
    assert len(h1) == 64

def test_compute_hash_different_values():
    r1 = normalize_business_key(sample_row())
    r2 = normalize_business_key(sample_row(category="锂金属"))
    assert compute_record_hash(r1) != compute_record_hash(r2)

def test_classify_valid():
    v, w, i, q = _classify_rows([sample_row()])
    assert len(v) == 1
    assert len(w) == 0
    assert len(i) == 0
    assert len(q) == 0

def test_classify_warning():
    r = sample_row(validation_status="warning", validation_message="平均价不在范围内")
    v, w, i, q = _classify_rows([r])
    assert len(w) == 1
    assert len(q) == 1
    assert q[0]["issue_level"] == "warning"

def test_classify_invalid():
    r = sample_row(validation_status="invalid", validation_message="最低价大于最高价")
    v, w, i, q = _classify_rows([r])
    assert len(i) == 1
    assert len(q) == 1
    assert q[0]["issue_level"] == "error"

def test_classify_mixed():
    rows = [
        sample_row(validation_status="valid", category="A", product_name="a"),
        sample_row(validation_status="warning", category="B", product_name="b"),
        sample_row(validation_status="invalid", category="C", product_name="c"),
    ]
    v, w, i, q = _classify_rows(rows)
    assert len(v) == 1
    assert len(w) == 1
    assert len(i) == 1
    assert len(q) == 2
