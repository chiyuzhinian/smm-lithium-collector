"""深入探测 hq.smm.cn 行情页面，找数据下载/导出功能或更多API。"""
import asyncio, sys, json, re
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from smm_collector.config import load_config
from smm_collector.browser import open_browser, close_browser

async def main():
    cfg = load_config(ROOT)
    pw, browser, context = await open_browser(cfg, headed=False, require_state=True)
    page = await context.new_page()

    url = "https://hq.smm.cn/new-energy/category/201102250059"
    print(f"访问: {url}")

    # 收集所有网络请求URL
    all_urls = set()

    async def capture_request(request):
        if request.resource_type in ("xhr", "fetch", "document"):
            all_urls.add(request.url)

    async def capture_response(response):
        if response.request.resource_type in ("xhr", "fetch"):
            try:
                body = await response.text()
                print(f"\n=== API: {response.url}")
                print(f"Status: {response.status}")
                print(f"Body({len(body)} chars): {body[:1000]}")
            except Exception:
                pass

    page.on("request", capture_request)
    page.on("response", capture_response)

    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(8000)

    # 检查页面元素
    # 查找下载/导出按钮
    for text in ["下载", "导出", "download", "export", "历史数据", "更多数据"]:
        try:
            btn = page.locator(f'*:has-text("{text}")')
            cnt = await btn.count()
            if cnt > 0:
                print(f"\n找到 '{text}' 相关元素: {cnt} 个")
                for i in range(min(cnt, 3)):
                    tag = await btn.nth(i).evaluate("el => el.tagName + ': ' + el.className + ' | ' + el.innerText.substring(0,100)")
                    print(f"  [{i}] {tag}")
        except Exception:
            pass

    # 检查日期选择器
    date_inputs = page.locator('input[type="date"], [class*="datepicker"], [class*="DatePicker"], [class*="calendar"]')
    date_cnt = await date_inputs.count()
    print(f"\n日期输入控件: {date_cnt} 个")

    # 查找所有a标签中可能的API/data链接
    links = page.locator('a')
    link_cnt = await links.count()
    api_links = set()
    for i in range(min(link_cnt, 200)):
        try:
            href = await links.nth(i).get_attribute("href")
            if href and any(k in href.lower() for k in ['ajax', 'api', 'data', 'download', 'export', 'history', 'excel', 'csv']):
                api_links.add(href)
        except Exception:
            pass
    print(f"\n数据/API相关链接: {len(api_links)}")
    for l in sorted(api_links):
        print(f"  {l}")

    # 保存截图
    out_dir = cfg.root / "data/raw/inspection" / f"hq2_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(out_dir / "page.png"), full_page=False)
    (out_dir / "page.html").write_text(await page.content(), encoding="utf-8")

    print(f"\n所有XHR/Fetch请求:")
    for u in sorted(all_urls):
        print(f"  {u}")

    await close_browser(pw, browser)

if __name__ == "__main__":
    asyncio.run(main())
