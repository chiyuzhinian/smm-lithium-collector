"""探测 hq.smm.cn 行情页面是否支持历史数据。"""
import asyncio, sys, json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from smm_collector.config import load_config
from smm_collector.browser import open_browser, close_browser
from smm_collector.network_capture import NetworkCapture

async def main():
    cfg = load_config(ROOT)
    pw, browser, context = await open_browser(cfg, headed=False, require_state=True)
    page = await context.new_page()

    # 测试几个 hq.smm.cn 行情页面
    test_urls = [
        "https://hq.smm.cn/new-energy/category/201102250059",  # 较早的category
        "https://hq.smm.cn/new-energy",  # 行情首页
    ]

    for url in test_urls:
        print(f"\n{'='*60}")
        print(f"访问: {url}")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = cfg.root / "data/raw/inspection" / f"hq_probe_{stamp}"
        out_dir.mkdir(parents=True, exist_ok=True)

        net = NetworkCapture(cfg.root / "data/raw/network")
        net.attach(page)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)

            title = await page.title()
            final_url = page.url
            print(f"标题: {title}")
            print(f"最终URL: {final_url}")

            # 查找日期相关元素
            date_elements = await page.locator('[class*="date"], [class*="Date"], [class*="picker"], [class*="Picker"], [class*="calendar"], input[type="date"], [class*="time"]').count()
            print(f"日期相关元素: {date_elements}")

            # 查找API请求 (检查页面源码中的API URL)
            html = await page.content()
            api_urls = []
            import re
            for match in re.finditer(r'https?://[^"\'<>]+(?:api|graphql|query|data)[^"\'<>]*', html, re.I):
                api_urls.append(match.group())
            print(f"页面中API URL数: {len(api_urls)}")
            for u in api_urls[:10]:
                print(f"  {u}")

            # 检查是否有日期选择器 input
            date_inputs = await page.locator('input').count()
            print(f"Input元素数: {date_inputs}")

            # 保存截图
            await page.screenshot(path=str(out_dir / "page.png"), full_page=False)
            (out_dir / "page.html").write_text(html, encoding="utf-8")

        except Exception as e:
            print(f"错误: {e}")

        await net.drain()
        print(f"网络请求捕获数: {len(net.candidates)}")
        for c in net.candidates[:10]:
            print(f"  {c['url']} [{c['status']}]")

    await close_browser(pw, browser)

if __name__ == "__main__":
    asyncio.run(main())
