from datetime import datetime
from smm_collector.parser import parse_category_section

def test_section_parser_does_not_mix_categories():
    html='''<div class="cat">锂金属</div><table><thead><tr><th>品名</th><th>日期</th></tr></thead><tbody><tr><td>金属锂</td><td>07-22</td></tr></tbody></table><div class="cat">锂矿</div><table><thead><tr><th>品名</th><th>日期</th></tr></thead><tbody><tr><td>锂辉石</td><td>07-22</td></tr></tbody></table>'''
    rows=parse_category_section(html,"锂矿",".cat",collected_at=datetime(2026,7,22))
    assert [r["product_name"] for r in rows]==["锂辉石"]
    assert all(r["category"]=="锂矿" for r in rows)
