from pathlib import Path

async def save_manual_login(config, context):
    dest = config.root / "data/auth/storage_state.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(dest))
    return dest

async def looks_logged_out(page, config) -> bool:
    """检测当前页是否已退出登录。

    旧版仅检查登录页跳转和「验证码/欢迎登录」等文本。
    SMM 于 2026-09-11 前后引入「登录墙」渲染：登录态失效时价格单元格被替换为
    `<span class="Unlogin_unlogin__Dwqcn"><img alt="登录" src=".../lock.png"></span>`，
    仍停留在原目标页（不跳转到登录页），旧检测器无法识别，导致采集器误判已登录、
    持续采集空价格入 DB。本函数增加对该形态的识别并返回 True（视为已退出登录）。
    """
    url = page.url.lower()
    if config.login_url:
        base = config.login_url.lower().rstrip("/")
        # 登录页常带 ?referer=...&errcode=10003 等查询串，用前缀匹配
        if url.startswith(base) or base.startswith(url.rstrip("/")):
            return True
    body = (await page.locator("body").inner_text())[:10000]
    signals = ["验证码", "短信验证", "滑块验证", "欢迎登录", "密码登录"]
    if any(x in body for x in signals):
        return True
    # 价格登录墙（2026-09-11 起）：Unlogin_ 类 + lock.png + alt="登录"。
    # 用更严格的组合信号避免「登录」单字误判登录入口链接。
    try:
        html_snippet = (await page.content())[:200000]
    except Exception:
        html_snippet = ""
    wall_signals = [
        'class="Unlogin_unlogin',
        'class="Unlogin_priceLocked',
        'src="/images/lock.png"',
        '/public/images/lock.png',
        'alt="登录"></span>',
    ]
    if any(s in html_snippet for s in wall_signals):
        return True
    return False


async def looks_price_locked(page, sample_rows: int = 30) -> bool:
    """抽样价格单元格：若首 sample_rows 行的平均价单元格均显示「登录锁」图标，
    则判定为登录墙（即便 looks_logged_out 因页面未跳登录页而漏检）。

    任意一行平均价单元格含数字 → 未被锁。
    """
    try:
        rows = await page.locator("table tbody tr").count()
    except Exception:
        return False
    if rows == 0:
        return False
    locked = 0
    checked = 0
    for i in range(min(rows, sample_rows)):
        try:
            cell_html = await page.locator("table tbody tr").nth(i).locator("td").nth(4).inner_html()
        except Exception:
            continue
        checked += 1
        if "Unlogin" in cell_html or "lock.png" in cell_html or 'alt="登录"' in cell_html:
            locked += 1
    return checked > 0 and locked == checked

async def guarded_auto_login(page, config):
    """Conservative login: refuses any verification challenge; selectors are user-configured."""
    if not (config.username and config.password and config.login_url): return False
    await page.goto(config.login_url, wait_until="domcontentloaded")
    text = (await page.locator("body").inner_text())[:10000]
    if any(x in text for x in ("验证码", "滑块", "短信验证")):
        raise RuntimeError("检测到验证挑战，请运行 manual_login.py 手动完成；程序不会绕过验证。")
    raise RuntimeError("自动登录需要基于 inspect_page.py 结果配置真实表单定位；请先使用手动登录。")

