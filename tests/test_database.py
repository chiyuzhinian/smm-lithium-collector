from datetime import date,datetime
from decimal import Decimal
from smm_collector.database import Database
from smm_collector.parser import record_hash
def row(category="锂矿",price="100"):
 r={"source":"SMM","market":"SMM锂电现货","category":category,"product_name":"同名产品","specification":"A","min_price":Decimal(price),"max_price":Decimal(price),"average_price":Decimal(price),"change_value":Decimal(0),"unit":"元/吨","price_date":date(2026,7,22),"collected_at":datetime(2026,7,22,9),"source_url":"x","collection_method":"DOM","raw_text":"x","extra_fields":"{}","validation_status":"valid","validation_message":""}; r["record_hash"]=record_hash(r); return r
def test_duplicate_and_update(tmp_path):
 db=Database(tmp_path/"x.db")
 assert db.upsert([row()])["inserted"]==1
 assert db.upsert([row()])["duplicate"]==1
 assert db.upsert([row(price="101")])["updated"]==1
def test_category_unique_key(tmp_path):
 db=Database(tmp_path/"x.db"); stats=db.upsert([row("锂矿"),row("锂金属")])
 assert stats["inserted"]==2
 with db.connect() as con: assert con.execute("select count(*) from lithium_spot_prices").fetchone()[0]==2

