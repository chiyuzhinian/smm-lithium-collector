"""测试字符串标准化函数。"""
from smm_collector.cleaner import normalize_str, normalize_unit, normalize_business_key

def test_normalize_str_trim():
    assert normalize_str("  电池级碳酸锂  ") == "电池级碳酸锂"

def test_normalize_str_collapse_spaces():
    assert normalize_str("元  /  吨") == "元 / 吨"

def test_normalize_str_fullwidth():
    # 全角数字/字母 → 半角
    assert normalize_str("ＡＢＣ１２３") == "ABC123"

def test_normalize_str_empty():
    assert normalize_str(None) == ""
    assert normalize_str("") == ""

def test_normalize_unit_slash():
    assert normalize_unit("元 / 吨") == "元/吨"
    assert normalize_unit("元／吨") == "元/吨"

def test_normalize_unit_trim():
    assert normalize_unit("  美元/千克  ") == "美元/千克"

def test_normalize_business_key_fields():
    row = {
        "source": "SMM", "market": "SMM锂电现货",
        "category": "锂化合物 ", "product_name": " 电池级碳酸锂 ",
        "specification": "Li₂CO₃≥99.5%", "unit": "元 / 吨",
        "price_date": "2026-07-22",
    }
    normalized = normalize_business_key(row)
    assert normalized["category"] == "锂化合物"
    assert normalized["product_name"] == "电池级碳酸锂"
    assert normalized["unit"] == "元/吨"
