"""手动登录SMM基础金属页面（铝/铜/镍），保存合并storage_state。"""
import asyncio, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from smm_collector.browser import open_browser, close_browser
from smm_collector.config import load_config

async def main():
    cfg = load_config(ROOT)
    # 打开可见浏览器，依次登录三个页面
    pw, browser, ctx = await open_browser(cfg, headed=True, require_state=False)
    pages = [
        ("https://hq.smm.cn/aluminum/category/201102250311", "铝"),
        ("https://hq.smm.cn/copper/category/201102250376", "铜"),
        ("https://hq.smm.cn/nickel/category/201102250239", "镍"),
    ]

    from asyncio import to_thread
    for url, label in pages:
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        print(f"\n请在浏览器中完成「{label}」页面的登录及验证码。")
        print(f"完成后回到此窗口按 Enter 继续下一个…")
        await to_thread(input)

    # 保存合并storage_state
    dest = cfg.root / "data/auth/storage_state.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    await ctx.storage_state(path=str(dest))
    print(f"\n登录状态已保存: {dest}")
    await close_browser(pw, browser)

asyncio.run(main())
