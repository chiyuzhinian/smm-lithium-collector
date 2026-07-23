from pathlib import Path
from playwright.async_api import async_playwright

async def open_browser(config, headed=False, require_state=True):
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=False if headed else config.settings["browser"]["headless"],
                                       slow_mo=config.settings["browser"]["slow_mo_ms"])
    state = config.root / "data/auth/storage_state.json"
    if require_state and not state.exists():
        await browser.close(); await pw.stop()
        raise RuntimeError("登录状态不存在，请先运行 python scripts/manual_login.py")
    context = await browser.new_context(storage_state=str(state) if state.exists() else None)
    context.set_default_timeout(config.settings["browser"]["timeout_ms"])
    return pw, browser, context

async def close_browser(pw, browser):
    await browser.close(); await pw.stop()

