"""铜铝镍延迟补采脚本。每天11:00运行，只采集9:00未成功获取的金属价格。"""
import asyncio, sys
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from smm_collector.browser import open_browser, close_browser
from smm_collector.config import load_config
from smm_collector.additional_sources import collect_source
from smm_collector.database import Database


async def main():
    cfg = load_config(ROOT)
    db = Database(cfg.path("database_path"))

    # Check which metals already have today's data
    today = date.today()
    con = db.connect()
    existing = {
        r[0] for r in con.execute(
            "SELECT product_name FROM lithium_spot_prices "
            "WHERE market='SMM基础金属现货' AND price_date=?",
            (str(today),)).fetchall()
    }
    con.close()

    items = cfg.additional_sources.get("items", [])
    retry_list = []
    for src in items:
        for prod in src.get("required_products", []):
            if prod.get("canonical_name", "") not in existing:
                retry_list.append(src)
                break

    if not retry_list:
        print(f"{today}: All metals already collected.")
        return

    print(f"{today}: Retrying {len(retry_list)} sources...")
    pw, browser, ctx = await open_browser(cfg, headed=False)
    page = await ctx.new_page()
    stamp = today.strftime("%Y-%m-%d_%H%M%S")

    for src in retry_list:
        rows, meta = await collect_source(page, src, today, stamp, cfg.path("raw_dir"))
        if rows:
            db.upsert(rows)
            for r in rows:
                print(f"  {r['product_name']}: {r['average_price']} {r['unit']} date={r['price_date']}")
        else:
            print(f"  {src.get('code')}: still unavailable")

    await close_browser(pw, browser)


if __name__ == "__main__":
    asyncio.run(main())
