from datetime import datetime
from pathlib import Path
import pytest
from smm_collector.parser import parse_html_tables,record_hash
FIX=Path(__file__).parent/"fixtures"
@pytest.mark.parametrize("name,file,count",[("锂金属","lithium_metal.html",1),("锂矿","lithium_ore.html",2),("锂化合物","lithium_compound.html",2)])
def test_all_category_html(name,file,count):
 rows=parse_html_tables((FIX/file).read_text(encoding="utf-8"),name,collected_at=datetime(2026,7,22))
 assert len(rows)==count and all(r["category"]==name for r in rows)
def test_extra_fields():
 row=parse_html_tables((FIX/"lithium_metal.html").read_text(encoding="utf-8"),"锂金属",collected_at=datetime(2026,7,22))[0]
 assert "产地" in row["extra_fields"]
def test_hash_consistency():
 row=parse_html_tables((FIX/"lithium_ore.html").read_text(encoding="utf-8"),"锂矿",collected_at=datetime(2026,7,22))[0]
 assert record_hash(row)==record_hash(dict(row))

