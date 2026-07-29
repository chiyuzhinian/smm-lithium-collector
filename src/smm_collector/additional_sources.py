"""附加数据源采集模块。

对配置中 additional_sources 的每个源进行页面采集，提取目标产品价格。
"""
from __future__ import annotations
import logging, json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from .parser import parse_html_tables
from .validator import validate_row

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
        title = await page.title()

        # 保存原始数据
        src_dir = raw_dir / source_cfg.get("code", name)
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / f"page_{stamp}.html").write_text(html, encoding="utf-8")
        await page.screenshot(path=str(src_dir / f"page_{stamp}.png"), full_page=True)

        # 解析所有表格
        category = source_cfg.get("category", "基础金属")
        all_rows = parse_html_tables(html, category)
        log.info("[%s] 解析到 %d 行", name, len(all_rows))

        # 筛选目标产品 - 使用 exact product matching
        collected = []
        for prod_cfg in required:
            canonical = prod_cfg.get("canonical_name", "")
            aliases = prod_cfg.get("aliases", [canonical])
            if not canonical:
                continue

            # Find matching row by product name in table
            matches = [r for r in all_rows
                       if any(alias in str(r.get("product_name", "")) for alias in aliases)]
            # Also search raw HTML text for product name
            if not matches:
                import re
                for alias in aliases:
                    if alias in html:
                        matches = _extract_from_page(html, alias, canonical, prod_cfg,
                                                     source_cfg, target_date, html)
                        break

            if not matches:
                # Try the historical price table - find today's row
                matches = _extract_from_historical(html, canonical, prod_cfg,
                                                   source_cfg, target_date)

            if matches:
                meta["found"].append(canonical)
                collected.extend(matches)
            else:
                meta["missing"].append(canonical)
                log.warning("[%s] 未找到目标产品: %s", name, canonical)

        # 校验每一行
        for row in collected:
            validate_row(row, target_date)

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
    """从历史价格表中提取当日价格。"""
    from bs4 import BeautifulSoup
    import re
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")

    for table in tables:
        tbody = table.select_one("tbody")
        if not tbody:
            continue
        rows = tbody.select("tr")
        for tr in rows:
            tds = tr.select("td")
            if len(tds) < 5:
                continue
            cells = [td.get_text(strip=True) for td in tds]
            # Check if this is a price data row (date, min, max, avg, change)
            if not re.match(r'\d{4}-\d{2}-\d{2}', cells[0]):
                continue
            row_date = cells[0]
            if row_date != str(target_date) and row_date != target_date.strftime("%Y-%m-%d"):
                continue
            # This is today's price row
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
                "price_date": date.fromisoformat(row_date) if row_date else target_date,
                "price_date_raw": row_date,
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

    return []


def _extract_from_page(html: str, alias: str, canonical: str, prod_cfg: dict,
                       src_cfg: dict, target_date: date, raw_html: str) -> list[dict]:
    """备选：从页面元素提取价格（当表格解析失败时）。"""
    # Already handled by _extract_from_historical as the primary method
    return _extract_from_historical(html, canonical, prod_cfg, src_cfg, target_date)


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
