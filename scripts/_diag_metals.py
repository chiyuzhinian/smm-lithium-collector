"""诊断三个SMM金属页面结构。"""
import asyncio, sys
sys.path.insert(0, "src")
from pathlib import Path
from smm_collector.browser import open_browser, close_browser
from smm_collector.config import load_config

async def diag(url, label):
    cfg = load_config(Path("."))
    pw, browser, ctx = await open_browser(cfg, headed=False)
    page = await ctx.new_page()
    print(f"\n=== {label}: {url} ===")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
        title = await page.title()
        print(f"Title: {title}")
        # Check login
        body = (await page.locator("body").inner_text())[:300]
        if "登录" in body or "验证码" in body:
            print("LOGIN REQUIRED!")
        else:
            print("Auth: OK")
        # Tables
        for s in ["table","thead","tbody","tr"]:
            print(f"  {s}: {await page.locator(s).count()}")
        # Find target products
        for target in ["A00铝","A00","1#电解铜","电解铜","1#电解镍","电解镍"]:
            loc = page.get_by_text(target, exact=False)
            cnt = await loc.count()
            if cnt > 0:
                txts = []
                for i in range(min(cnt,5)):
                    try: txts.append((await loc.nth(i).inner_text()).strip()[:120])
                    except: pass
                print(f"  '{target}' found({cnt}): {txts[:3]}")
        # Table headers
        for i in range(min(await page.locator("table").count(),2)):
            t = page.locator("table").nth(i)
            ths = []; thc = await t.locator("th").count()
            for j in range(min(thc,10)):
                try: ths.append((await t.locator("th").nth(j).inner_text()).strip())
                except: pass
            print(f"  Table{i} headers: {ths}")
            trs = t.locator("tbody tr")
            trc = await trs.count()
            if trc > 0:
                cells = []
                ccount = await trs.nth(0).locator("td").count()
                for j in range(min(ccount,10)):
                    try: cells.append((await trs.nth(0).locator("td").nth(j).inner_text()).strip()[:80])
                    except: pass
                print(f"  Table{i} rows={trc} first_row({ccount}): {cells}")
        await page.screenshot(path=f"data/screenshots/diag_{label}.png", full_page=True)
    finally:
        await close_browser(pw, browser)

async def main():
    for url, label in [
        ("https://hq.smm.cn/aluminum/category/201102250311","aluminum"),
        ("https://hq.smm.cn/copper/category/201102250376","copper"),
        ("https://hq.smm.cn/nickel/category/201102250239","nickel"),
    ]:
        await diag(url, label)

asyncio.run(main())
