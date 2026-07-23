from __future__ import annotations
import hashlib, json, re
from datetime import datetime
from bs4 import BeautifulSoup
from .cleaner import parse_decimal, parse_price_date, decimal_text

ALIASES = {
 "product_name": ("品名", "产品名称", "名称"), "specification": ("规格", "牌号"),
 "min_price": ("最低价", "低价"), "max_price": ("最高价", "高价"),
 "average_price": ("平均价", "均价", "价格"), "change_value": ("涨跌", "涨跌幅"),
 "unit": ("单位",), "price_date": ("日期", "更新时间")}

def _norm(s): return re.sub(r"\s+", "", s or "")
def record_hash(row):
    keys=("category","product_name","specification","min_price","max_price","average_price","change_value","unit","price_date")
    data=[]
    for k in keys:
        value = decimal_text(row.get(k)) if k.endswith("price") or k == "change_value" else str(row.get(k) or "")
        data.append(value if value is not None else "")
    return hashlib.sha256("\x1f".join(data).encode("utf-8")).hexdigest()

def parse_html_tables(html, category, source_url="", collected_at=None):
    soup=BeautifulSoup(html,"lxml"); results=[]; collected_at=collected_at or datetime.now()
    for table in soup.find_all("table"):
        headers=[_norm(x.get_text(" ",strip=True)) for x in table.select("thead th")]
        if not headers:
            first=table.find("tr"); headers=[_norm(x.get_text(" ",strip=True)) for x in first.find_all(["th","td"])] if first else []
        if not any(h in sum((list(v) for v in ALIASES.values()),[]) for h in headers): continue
        rows=table.select("tbody tr") or table.select("tr")[1:]
        for tr in rows:
            cells=[x.get_text(" ",strip=True) for x in tr.find_all(["td","th"])]
            if not cells or len(cells)!=len(headers): continue
            raw=dict(zip(headers,cells)); mapped={}
            used=set()
            for field,names in ALIASES.items():
                for name in names:
                    if name in raw: mapped[field]=raw[name]; used.add(name); break
            if not mapped.get("product_name"): continue
            extra={k:v for k,v in raw.items() if k not in used}
            row={"source":"SMM","market":"SMM锂电现货","category":category,
                 "product_name":mapped.get("product_name",""),"specification":mapped.get("specification",""),
                 "min_price":parse_decimal(mapped.get("min_price")),"max_price":parse_decimal(mapped.get("max_price")),
                 "average_price":parse_decimal(mapped.get("average_price")),"change_value":parse_decimal(mapped.get("change_value")),
                 "unit":mapped.get("unit",""),"price_date":parse_price_date(mapped.get("price_date"),collected_at),
                 "price_date_raw":mapped.get("price_date",""),"collected_at":collected_at.replace(microsecond=0),
                 "source_url":source_url,"collection_method":"DOM","raw_text":tr.get_text(" | ",strip=True),
                 "extra_fields":json.dumps(extra,ensure_ascii=False,sort_keys=True)}
            row["record_hash"]=record_hash(row); results.append(row)
    return results

def parse_category_section(html, category, heading_selector, source_url="", collected_at=None):
    """Parse only tables belonging to one diagnosed category section."""
    soup=BeautifulSoup(html,"lxml")
    headings=soup.select(heading_selector)
    target=next((h for h in headings if _norm(h.get_text(" ",strip=True))==_norm(category)),None)
    if target is None: return []
    parts=[]
    for node in target.find_all_next():
        if node is not target and getattr(node,"name",None) and node.select_one(heading_selector) is target:
            continue
        if node in headings and node is not target: break
        if getattr(node,"name",None)=="table":
            parts.append(str(node))
    # A heading may be nested in a wrapper, so the next heading is not always in
    # its descendant stream. The diagnosed layout has one direct following table.
    if not parts:
        table=target.find_next("table")
        if table is not None: parts=[str(table)]
    return parse_html_tables("<html><body>"+"".join(parts)+"</body></html>",category,source_url,collected_at)

def extract_json_records(payload):
    """Find tabular record lists without assuming a site-private response schema."""
    found=[]
    def walk(x):
        if isinstance(x,list):
            if x and all(isinstance(v,dict) for v in x): found.append(x)
            for v in x: walk(v)
        elif isinstance(x,dict):
            for v in x.values(): walk(v)
    walk(payload); return max(found,key=len,default=[])
