"""从 SMM 页面提取分类名 → product_id 映射。"""
import asyncio, sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from smm_collector.config import load_config
from smm_collector.browser import open_browser, close_browser

async def main():
    cfg = load_config(ROOT)
    pw, browser, context = await open_browser(cfg, headed=False, require_state=True)
    page = await context.new_page()

    await page.goto(cfg.target_url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(5000)

    # 找到所有分类标题元素
    heading_selector = cfg.selectors.get("category", {}).get("item")
    elements = page.locator(heading_selector)
    count = await elements.count()
    print(f"找到 {count} 个分类标题")

    mappings = []
    for i in range(count):
        el = elements.nth(i)
        name = (await el.inner_text()).strip()

        # 在当前标题附近查找 hq.smm.cn 链接
        # 方法1: 检查标题元素内部的链接
        links_in_heading = el.locator("a")
        link_count = await links_in_heading.count()

        product_id = None
        if link_count > 0:
            href = await links_in_heading.first.get_attribute("href")
            if href and "hq.smm.cn/new-energy/category/" in (href or ""):
                import re
                m = re.search(r'/category/(\d+)', href)
                if m:
                    product_id = m.group(1)

        # 方法2: 在标题的下一个table中查找链接
        if not product_id:
            try:
                table = el.locator("xpath=following::table[1]")
                if await table.count() > 0:
                    links = table.locator("a[href*='hq.smm.cn/new-energy/category/']")
                    if await links.count() > 0:
                        href = await links.first.get_attribute("href")
                        import re
                        m = re.search(r'/category/(\d+)', href or "")
                        if m:
                            product_id = m.group(1)
            except Exception:
                pass

        # 方法3: 在标题之后的区域中查找（扩大搜索范围）
        if not product_id:
            try:
                # 查找标题后500px范围内的链接
                box = await el.bounding_box()
                if box:
                    links = page.locator(f'a[href*="hq.smm.cn/new-energy/category/"]')
                    link_count2 = await links.count()
                    for j in range(min(link_count2, 50)):
                        link = links.nth(j)
                        link_box = await link.bounding_box()
                        if link_box and abs(link_box["y"] - box["y"]) < 800:
                            href = await link.get_attribute("href")
                            import re
                            m = re.search(r'/category/(\d+)', href or "")
                            if m:
                                product_id = m.group(1)
                                break
            except Exception:
                pass

        mappings.append({"category": name, "product_id": product_id})
        print(f"  [{i+1}] {name} → {product_id or 'NOT FOUND'}")

    # 保存映射
    out = cfg.root / "data/raw/inspection" / "category_product_mapping.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(mappings, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n映射已保存到: {out}")
    print(f"已映射: {sum(1 for m in mappings if m['product_id'])}/{len(mappings)}")

    await close_browser(pw, browser)

if __name__ == "__main__":
    asyncio.run(main())
