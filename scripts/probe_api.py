"""探测 hq.smm.cn 历史数据 API，获取完整返回内容。"""
import asyncio, sys, json
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

    # 访问一个 hq.smm.cn 行情页面，拦截所有 XHR/Fetch
    url = "https://hq.smm.cn/new-energy/category/201102250059"
    print(f"访问: {url}")

    # 收集所有API响应
    api_responses = []

    async def handle_response(response):
        if response.request.resource_type in ("xhr", "fetch"):
            try:
                body = await response.text()
                api_responses.append({
                    "url": response.url,
                    "status": response.status,
                    "body": body[:5000]  # 前5000字符
                })
            except Exception:
                pass

    page.on("response", handle_response)

    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(8000)  # 等图表API加载

    print(f"\n捕获 {len(api_responses)} 个API响应\n")

    for i, r in enumerate(api_responses):
        print(f"{'='*60}")
        print(f"[{i+1}] {r['url']}")
        print(f"状态: {r['status']}")
        print(f"内容(前2000字符): {r['body'][:2000]}")
        print()

    # 保存到文件
    out_dir = cfg.root / "data/raw/inspection" / f"api_probe_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, r in enumerate(api_responses):
        (out_dir / f"api_{i+1}.json").write_text(
            json.dumps({"url": r["url"], "status": r["status"], "body": r["body"]},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已保存到: {out_dir}")

    await close_browser(pw, browser)

if __name__ == "__main__":
    asyncio.run(main())
