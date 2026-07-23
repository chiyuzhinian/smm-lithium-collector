from datetime import datetime
from decimal import Decimal
import pytest
from smm_collector.cleaner import parse_decimal,parse_price_date
@pytest.mark.parametrize("raw,want",[("143926",Decimal("143926")),("143,926",Decimal("143926")),("18.48",Decimal("18.48")),("-0.125",Decimal("-0.125")),("-1,045",Decimal("-1045")),("--",None),("暂无",None),("",None)])
def test_numbers(raw,want): assert parse_decimal(raw)==want
def test_mmdd(): assert str(parse_price_date("07-22",datetime(2026,7,22)))=="2026-07-22"
def test_cross_year(): assert str(parse_price_date("12-31",datetime(2027,1,2)))=="2026-12-31"

