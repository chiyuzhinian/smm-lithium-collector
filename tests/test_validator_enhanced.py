"""测试增强后的校验规则。"""
from datetime import date
from decimal import Decimal
from smm_collector.validator import validate_row, check_price_volatility

def base_row(**kw):
    r = {"source": "SMM", "market": "SMM锂电现货", "category": "锂矿",
         "product_name": "矿石", "price_date": date(2026, 7, 22),
         "unit": "元/吨", "min_price": Decimal(1), "max_price": Decimal(3),
         "average_price": Decimal(2)}
    r.update(kw)
    return r

def test_negative_price():
    r = base_row(min_price=Decimal(-1))
    result = validate_row(r)
    assert result["validation_status"] == "invalid"
    assert "负数" in result["validation_message"]

def test_empty_source():
    r = base_row(source="")
    result = validate_row(r)
    assert result["validation_status"] == "invalid"

def test_date_year_anomaly():
    r = base_row(price_date=date(1990, 1, 1))
    result = validate_row(r)
    assert result["validation_status"] == "invalid"

def test_large_price_warning():
    r = base_row(max_price=Decimal("9999999999"))
    result = validate_row(r)
    assert result["validation_status"] == "warning"

def test_volatility_normal():
    level, ratio = check_price_volatility(Decimal(100), Decimal(95))
    assert level is None
    assert ratio is not None and ratio < 0.30

def test_volatility_warning():
    level, ratio = check_price_volatility(Decimal(150), Decimal(100))
    assert level == "warning"
    assert ratio is not None and ratio >= 0.30

def test_volatility_error():
    level, ratio = check_price_volatility(Decimal(250), Decimal(100))
    assert level == "error"
    assert ratio is not None and ratio >= 1.0

def test_volatility_none():
    level, ratio = check_price_volatility(None, Decimal(100))
    assert level is None
    assert ratio is None

def test_change_value_negative_ok():
    r = base_row(change_value=Decimal(-500))
    result = validate_row(r)
    assert result["validation_status"] == "valid"
