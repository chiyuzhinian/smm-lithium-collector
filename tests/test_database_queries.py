"""测试 database.py 新增的多日查询方法。"""
from datetime import date, datetime
from decimal import Decimal
from smm_collector.database import Database
from smm_collector.parser import record_hash


def _row(category="锂矿", product_name="锂辉石", price_date="2026-07-22", avg="100"):
	r = {
		"source": "SMM", "market": "SMM锂电现货", "category": category,
		"product_name": product_name, "specification": "A", "unit": "元/吨",
		"min_price": Decimal(avg), "max_price": Decimal(avg),
		"average_price": Decimal(avg), "change_value": Decimal(0),
		"price_date": date.fromisoformat(price_date),
		"collected_at": datetime(2026, 7, 23, 9),
		"source_url": "x", "collection_method": "DOM", "raw_text": "x",
		"extra_fields": "{}", "validation_status": "valid", "validation_message": "",
	}
	r["record_hash"] = record_hash(r)
	return r


def test_get_latest_price_dates(tmp_path):
	db = Database(tmp_path / "test.db")
	db.upsert([_row(price_date="2026-07-20"), _row(price_date="2026-07-22"), _row(price_date="2026-07-23")])
	dates = db.get_latest_price_dates(3)
	assert dates == ["2026-07-23", "2026-07-22", "2026-07-20"]


def test_get_latest_2_of_5(tmp_path):
	db = Database(tmp_path / "test.db")
	for d in ["2026-07-17", "2026-07-18", "2026-07-21", "2026-07-22", "2026-07-23"]:
		db.upsert([_row(price_date=d, product_name=f"P{d}")])
	dates = db.get_latest_price_dates(3)
	assert dates == ["2026-07-23", "2026-07-22", "2026-07-21"]


def test_distinct_count(tmp_path):
	db = Database(tmp_path / "test.db")
	for d in ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23"]:
		db.upsert([_row(price_date=d, product_name=f"P{d}")])
	assert db.get_distinct_price_date_count() == 4


def test_get_records_by_dates(tmp_path):
	db = Database(tmp_path / "test.db")
	db.upsert([
		_row(product_name="A", price_date="2026-07-22"),
		_row(product_name="B", price_date="2026-07-23"),
		_row(product_name="C", price_date="2026-07-21"),
	])
	records = db.get_records_by_price_dates(["2026-07-22", "2026-07-23"])
	assert len(records) == 2
	names = {r["product_name"] for r in records}
	assert names == {"A", "B"}


def test_exclude_invalid(tmp_path):
	db = Database(tmp_path / "test.db")
	r_invalid = _row(product_name="X", price_date="2026-07-23")
	r_invalid["validation_status"] = "invalid"
	r_valid = _row(product_name="Y", price_date="2026-07-23")
	db.upsert([r_invalid, r_valid])
	records = db.get_records_by_price_dates(["2026-07-23"], exclude_invalid=True)
	assert len(records) == 1
	assert records[0]["product_name"] == "Y"
