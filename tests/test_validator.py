from datetime import date
from decimal import Decimal
from smm_collector.validator import validate_row,run_status
def base():
    return {"source":"SMM","market":"SMM锂电现货","category":"锂矿",
            "product_name":"矿","price_date":date(2026,7,22),"unit":"元/吨",
            "min_price":Decimal(1),"max_price":Decimal(3),"average_price":Decimal(2)}
def test_min_greater_than_max():
    r=base(); r.update(min_price=Decimal(4)); assert validate_row(r)["validation_status"]=="invalid"
def test_average_range():
    r=base(); r.update(average_price=Decimal(4)); assert validate_row(r)["validation_status"]=="warning"
def test_status():
    expected=["锂金属","锂矿","锂化合物"]
    assert run_status(expected,expected)=="success"
    assert run_status(expected,["锂矿"])=="partial_success"
    assert run_status(expected,[])=="failed"
def test_status_large_list():
    large=["A","B","C","D","E","F","G","H","I","J"]
    assert run_status(large,large)=="success"
    assert run_status(large,["A","C"])=="partial_success"
    assert run_status(large,[])=="failed"
