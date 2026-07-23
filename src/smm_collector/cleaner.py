from __future__ import annotations
import re, unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

NULLS = {"", "--", "-", "暂无", "null", "none", "n/a"}

# ── 字符串标准化 ──────────────────────────────────────────────

def normalize_str(value: str | None) -> str:
    """标准化字符串：去首尾空格、合并连续空格、统一 Unicode、全角转半角。"""
    if value is None:
        return ""
    text = str(value).strip()
    # Unicode NFKC 规范化（全角数字/字母转半角）
    text = unicodedata.normalize("NFKC", text)
    # 全角符号转半角
    result = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            result.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            result.append(" ")
        else:
            result.append(ch)
    text = "".join(result)
    # 合并连续空格
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_unit(value: str | None) -> str:
    """标准化单位字段：统一空格和斜杠形式。"""
    if value is None:
        return ""
    text = normalize_str(value)
    # 统一斜杠：全角 ／ → 半角 /
    text = text.replace("／", "/")
    # 统一 "元 / 吨" → "元/吨"
    text = re.sub(r"\s*/\s*", "/", text)
    return text


def normalize_business_key(row: dict) -> dict:
    """对业务唯一键相关字段执行标准化，返回标准化后的副本。"""
    key_fields = ["source", "market", "category", "product_name", "specification", "unit"]
    result = dict(row)
    for f in key_fields:
        if f in result:
            result[f] = normalize_str(result.get(f))
    if "unit" in result:
        result["unit"] = normalize_unit(result["unit"])
    if "price_date" in result:
        pd_val = result.get("price_date")
        if isinstance(pd_val, date):
            result["price_date"] = pd_val.isoformat()
        elif pd_val:
            result["price_date"] = str(pd_val).strip()
    return result


# ── 数值和日期解析 ────────────────────────────────────────────
def parse_decimal(value):
    if value is None: return None
    text = str(value).strip().lower()
    if text in NULLS: return None
    text = text.replace(",", "").replace("−", "-")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match: return None
    try: return Decimal(match.group())
    except InvalidOperation: return None

def parse_price_date(value, collected_at: datetime | None = None):
    if value is None or not str(value).strip(): return None
    now = collected_at or datetime.now(); text = str(value).strip().replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y年%m月%d日"):
        try: return datetime.strptime(text, fmt).date()
        except ValueError: pass
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", text)
    if not m: return None
    candidate = date(now.year, int(m.group(1)), int(m.group(2)))
    # A future-looking date > 31 days is the preceding year (Jan collecting Dec data).
    if (candidate - now.date()).days > 31: candidate = date(now.year - 1, candidate.month, candidate.day)
    return candidate

def decimal_text(value):
    if value is None: return None
    return format(value, "f") if isinstance(value, Decimal) else str(value)

