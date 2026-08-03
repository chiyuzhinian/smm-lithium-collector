"""附加数据源采集模块。"""
from __future__ import annotations
import json, logging, re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

log = logging.getLogger("smm_collector.additional")


async def collect_source(page, source_cfg: dict, target_date: date, stamp: str,
                         raw_dir: Path) -> tuple[list[dict], dict]:
    """采集单个附加数据源，返回 (rows, meta)。"""
    name = source_cfg.get("name", source_cfg.get("code", "unknown"))
    url = source_cfg.get("url", "")
    required = source_cfg.get("required_products", [])

    meta = {"name": name, "url": url, "status": "failed", "found": [], "missing": [],
            "ambiguous": [], "error": "", "row_count": 0}

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        html = await page.content()

        # 保存原始数据
        src_dir = raw_dir / source_cfg.get("code", name)
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / f"page_{stamp}.html").write_text(html, encoding="utf-8")
        await page.screenshot(path=str(src_dir / f"page_{stamp}.png"), full_page=True)

        # 筛选目标产品
        collected = []
        for prod_cfg in required:
            canonical = prod_cfg.get("canonical_name", "")
            if not canonical:
                continue

            matches = _extract_from_historical(html, canonical, prod_cfg, source_cfg, target_date)

            if matches:
                meta["found"].append(canonical)
                collected.extend(matches)
            else:
                meta["missing"].append(canonical)
                log.warning("[%s] 未找到目标产品: %s", name, canonical)

        meta["row_count"] = len(collected)
        meta["status"] = "success" if not meta["missing"] else "partial_success"
        return collected, meta

    except Exception as e:
        meta["error"] = str(e)
        meta["status"] = "failed"
        log.error("[%s] 采集失败: %s", name, e)
        return [], meta


def _extract_from_historical(html: str, canonical: str, prod_cfg: dict,
                             src_cfg: dict, target_date: date) -> list[dict]:
    """从历史价格表中提取最新可用价格。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")

    latest_date = None
    latest_cells = None
    for table in soup.find_all("table"):
        for tr in table.select("tbody tr"):
            tds = tr.select("td")
            if len(tds) < 5:
                continue
            cells = [td.get_text(strip=True) for td in tds]
            if not re.match(r'\d{4}-\d{2}-\d{2}', cells[0]):
                continue
            d = cells[0]
            if latest_date is None or d > latest_date:
                latest_date = d
                latest_cells = cells

    if not latest_date or not latest_cells:
        return []

    # 检查最新日期是否在陈旧阈值内
    stale_days = src_cfg.get("stale_after_days", 5)
    latest_d = date.fromisoformat(latest_date)
    if (target_date - latest_d).days > stale_days:
        log.warning("最新数据日期 %s 超过陈旧阈值 %d 天", latest_date, stale_days)
        return []

    cells = latest_cells
    row = {
        "source": src_cfg.get("source", "SMM"),
        "market": src_cfg.get("market", "SMM基础金属现货"),
        "category": src_cfg.get("category", "基础金属"),
        "product_name": canonical,
        "specification": prod_cfg.get("specification", ""),
        "min_price": _dec(cells[1]) if len(cells) > 1 else None,
        "max_price": _dec(cells[2]) if len(cells) > 2 else None,
        "average_price": _dec(cells[3]) if len(cells) > 3 else None,
        "change_value": _dec(cells[4]) if len(cells) > 4 else None,
        "unit": prod_cfg.get("unit", "元/吨"),
        "price_date": latest_d,
        "price_date_raw": latest_date,
        "collected_at": datetime.now(),
        "source_url": src_cfg.get("url", ""),
        "collection_method": "DOM",
        "raw_text": " | ".join(cells),
        "extra_fields": json.dumps({
            "material_attribute": prod_cfg.get("material_attribute", ""),
            "info_category": prod_cfg.get("info_category", ""),
            "chemistry": prod_cfg.get("chemistry", ""),
        }, ensure_ascii=False),
    }
    from .parser import record_hash
    row["record_hash"] = record_hash(row)
    row["validation_status"] = "valid"
    row["validation_message"] = ""
    return [row]


def _dec(val):
    """安全转换字符串为 Decimal。"""
    if val is None:
        return None
    try:
        text = str(val).strip().replace(",", "").replace("+", "").replace(" ", "")
        if text in ("", "--", "-", "暂无"):
            return None
        return Decimal(text)
    except Exception:
        return None
