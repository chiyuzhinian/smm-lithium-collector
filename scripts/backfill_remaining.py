"""补采缺失的15个分类 — 一次性完成，节省浏览器启动开销。"""
import asyncio, sys, json, hashlib, re
from pathlib import Path
from datetime import date, datetime
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from smm_collector.config import load_config
from smm_collector.browser import open_browser, close_browser
from smm_collector.database import Database
from smm_collector.validator import validate_row
from smm_collector.cleaner import parse_price_date

HISTORY_API = "https://hq.smm.cn/ajax/spot/history/{product_id}/{start}/{end}"
HQ_PAGE = "https://hq.smm.cn/new-energy/category/{product_id}"


def compute_record_hash(row: dict) -> str:
    keys = ("category", "product_name", "specification", "min_price",
            "max_price", "average_price", "change_value", "unit", "price_date")
    data = []
    for k in keys:
        v = row.get(k)
        if k.endswith("price") or k == "change_value":
            from smm_collector.cleaner import decimal_text
            data.append(decimal_text(v) if v is not None else "")
        else:
            data.append(str(v or ""))
    return hashlib.sha256("\x1f".join(data).encode("utf-8")).hexdigest()


async def browser_fetch(page, url: str) -> dict | None:
    try:
        return await page.evaluate("""
            async (url) => {
                const resp = await fetch(url);
                if (!resp.ok) return null;
                return await resp.json();
            }
        """, url)
    except Exception:
        return None


async def get_product_name(page, product_id: str) -> str:
    url = HQ_PAGE.format(product_id=product_id)
    try:
        html = await page.evaluate("""
            async (url) => {
                const resp = await fetch(url);
                if (!resp.ok) return '';
                return await resp.text();
            }
        """, url)
        if html:
            m = re.search(r'<title>([^_<]+)', html)
            if m:
                title = m.group(1).strip()
                title = re.sub(r'(今日价格|价格走势图|价格查询|现货价格|市场价.*)$', '', title)
                return title
    except Exception:
        pass
    return ""


async def establish_waf_session(page, cfg) -> bool:
    await page.goto("https://hq.smm.cn/new-energy/category/201102250059",
                    wait_until="domcontentloaded", timeout=30000)
    title = await page.title()
    print(f"  hq.smm.cn: {title}")
    if "WAF" in title:
        print("  等待 WAF 验证…")
        for i in range(5):
            await asyncio.sleep(3)
            test_url = HISTORY_API.format(product_id="202212050001", start="2026-06-23", end="2026-07-23")
            test_result = await browser_fetch(page, test_url)
            if test_result and test_result.get("code") == 0 and test_result.get("data"):
                print(f"  WAF 通过（{i+1}次后）")
                return True
        return False
    else:
        await page.wait_for_timeout(3000)
        return True


def api_to_row(api_item: dict, category: str, product_name: str,
               collected_at: datetime) -> dict:
    price_date = parse_price_date(api_item["renew_date"])
    row = {
        "source": "SMM", "market": "SMM锂电现货", "category": category,
        "product_name": product_name or category, "specification": "",
        "min_price": Decimal(str(api_item.get("low", 0))),
        "max_price": Decimal(str(api_item.get("highs", 0))),
        "average_price": Decimal(str(api_item.get("average", 0))),
        "change_value": Decimal(str(api_item.get("vchange", 0))),
        "unit": "", "price_date": price_date,
        "collected_at": collected_at,
        "source_url": HQ_PAGE.format(product_id=api_item.get("product_id", "")),
        "collection_method": "API",
        "raw_text": json.dumps(api_item, ensure_ascii=False),
        "extra_fields": json.dumps({
            "vchange_rate": api_item.get("vchange_rate"),
            "point_precision": api_item.get("point_precision"),
            "change_rate_show": api_item.get("change_rate_show"),
        }, ensure_ascii=False),
        "validation_status": "valid", "validation_message": "",
    }
    row["record_hash"] = compute_record_hash(row)
    return row

MISSING = [
    "人造石墨", "天然石墨", "天然石墨负极", "新型负极",
    "镍化合物", "溶剂及相关原料", "焦类", "电解液",
    "钴化合物", "钴矿", "钴金属", "锂矿", "锂金属",
    "锰化合物", "正极材料",
]

async def main():
    cfg = load_config(ROOT)
    mapping_file = cfg.root / "data/raw/inspection/category_product_mapping.json"
    all_mappings = json.loads(mapping_file.read_text(encoding="utf-8"))
    targets = [m for m in all_mappings if m["category"] in MISSING]

    today = date.today()
    start_str = today.replace(year=today.year - 1).isoformat()
    end_str = today.isoformat()

    print(f"补采 {len(targets)} 个缺失分类")

    pw, browser, context = await open_browser(cfg, headed=False, require_state=True)
    page = await context.new_page()

    try:
        if not await establish_waf_session(page, cfg):
            print("WAF会话建立失败")
            return

        print(f"\n拉取数据 ({start_str} ~ {end_str})…")
        all_rows = []
        stats = {}

        for i, m in enumerate(targets):
            pid = m["product_id"]
            cat = m["category"]

            # 获取产品名
            pname = await get_product_name(page, pid)
            if not pname:
                pname = cat

            # 拉取数据
            url = HISTORY_API.format(product_id=pid, start=start_str, end=end_str)
            raw = None
            for attempt in range(3):
                raw = await browser_fetch(page, url)
                if raw and raw.get("code") == 0 and raw.get("data"):
                    break
                if attempt < 2:
                    await asyncio.sleep(2)

            if not raw or not raw.get("data"):
                print(f"  [{i+1:2d}/{len(targets)}] {cat:12s} → 无数据")
                stats[cat] = 0
                continue

            items = raw["data"]
            now = datetime.now()
            rows = [api_to_row(item, cat, pname, now) for item in items]
            validated = [validate_row(r) for r in rows]
            all_rows.extend(validated)

            dates = sorted(set(str(r["price_date"]) for r in validated if r["price_date"]))
            date_range = f"{dates[0]} ~ {dates[-1]}" if dates else "N/A"
            print(f"  [{i+1:2d}/{len(targets)}] {cat:12s} → {len(validated)}条 ({date_range})  {pname[:30]}")
            stats[cat] = len(validated)
            await asyncio.sleep(0.3)

    finally:
        await close_browser(pw, browser)

    print(f"\n存储: 共 {len(all_rows)} 条")
    if all_rows:
        db = Database(cfg.path("database_path"))
        db_stats = db.upsert(all_rows)
        print(f"  新增 {db_stats['inserted']} / 更新 {db_stats['updated']} / 重复 {db_stats['duplicate']}")

    success = sum(1 for n in stats.values() if n > 0)
    failed_list = [c for c, n in stats.items() if n == 0]
    print(f"成功 {success}/{len(targets)}")
    if failed_list:
        print(f"失败: {', '.join(failed_list)}")

if __name__ == "__main__":
    asyncio.run(main())
