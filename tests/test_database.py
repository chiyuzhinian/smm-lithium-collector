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

def test_duplicate_refreshes_collected_at(tmp_path):
 """数据完全一致（duplicate）时也应刷新 collected_at（门户采集更新时间语义）。"""
 db=Database(tmp_path/"x.db")
 r1=row(); r1["collected_at"]=datetime(2026,7,22,9,0,0)
 assert db.upsert([r1])["inserted"]==1
 with db.connect() as con:
  updated_before=con.execute("SELECT updated_at FROM lithium_spot_prices").fetchone()[0]
 # 第二天再采集同一数据（值不变）：duplicate 但 collected_at 刷新
 r2=row(); r2["collected_at"]=datetime(2026,7,23,9,5,0)
 stats=db.upsert([r2])
 assert stats["duplicate"]==1
 with db.connect() as con:
  got=con.execute("SELECT collected_at, updated_at FROM lithium_spot_prices").fetchone()
  assert got[0]=="2026-07-23 09:05:00"  # collected_at 已刷新
  assert got[1]==updated_before          # updated_at 不变（数据未变化）
def test_category_unique_key(tmp_path):
 db=Database(tmp_path/"x.db"); stats=db.upsert([row("锂矿"),row("锂金属")])
 assert stats["inserted"]==2
 with db.connect() as con: assert con.execute("select count(*) from lithium_spot_prices").fetchone()[0]==2

