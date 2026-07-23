from pathlib import Path

async def save_manual_login(config, context):
    dest = config.root / "data/auth/storage_state.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(dest))
    return dest

async def looks_logged_out(page, config) -> bool:
    url = page.url.lower()
    if config.login_url and url.rstrip("/") == config.login_url.lower().rstrip("/"): return True
    signals = ["验证码", "短信验证", "滑块验证"]
    body = (await page.locator("body").inner_text())[:10000]
    return any(x in body for x in signals)

async def guarded_auto_login(page, config):
    """Conservative login: refuses any verification challenge; selectors are user-configured."""
    if not (config.username and config.password and config.login_url): return False
    await page.goto(config.login_url, wait_until="domcontentloaded")
    text = (await page.locator("body").inner_text())[:10000]
    if any(x in text for x in ("验证码", "滑块", "短信验证")):
        raise RuntimeError("检测到验证挑战，请运行 manual_login.py 手动完成；程序不会绕过验证。")
    raise RuntimeError("自动登录需要基于 inspect_page.py 结果配置真实表单定位；请先使用手动登录。")

