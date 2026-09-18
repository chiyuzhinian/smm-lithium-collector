from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger("smm_collector.authentication")

# ── 验证挑战 / 登录失败 / 成功关键字 ────────────────────────────────
# 与 auth_health.py 的 VERIFICATION_SIGNALS / LOGIN_PROMPT_SIGNALS 保持一致，
# 此处再独立维护是因为 Phase C 自动登录失败判定需要更细的字面匹配。
VERIFICATION_KEYWORDS = (
    "验证码", "短信验证", "滑块验证", "安全验证", "风险检测",
    "图形验证", "扫码登录", "扫码验证", "人机验证",
)

# 登录失败关键字（出现在错误提示中）。
LOGIN_FAILED_KEYWORDS = (
    "账号或密码错误", "用户名或密码错误", "账号不存在",
    "密码错误", "登录失败", "账号被锁定", "账号已冻结",
    "invalid", "incorrect", "wrong password", "account locked",
)

# 登录成功判定：URL 离开登录页 / body 出现「登录墙 DOM」消失等。
LOGIN_SUBMIT_WAIT_SECONDS = 15

# Phase C：单次自动登录尝试的最大值由 config 控制；此处记录进程内已尝试次数，
# 防止 main.py 重试循环中累计超过配置上限。
_AUTO_LOGIN_ATTEMPTS_KEY = "_auto_login_attempts_used"


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


# ── Phase C：自动账号密码续登 ────────────────────────────────────────


def _selectors_from_config(config) -> dict:
    """读取 config.selectors.login 段（user 可在 selectors.yaml 自定义）。"""
    try:
        sel = (config.selectors or {}).get("login") or {}
    except Exception:
        sel = {}
    return {
        "username": sel.get("username"),
        "password": sel.get("password"),
        "submit": sel.get("submit"),
        "form": sel.get("form"),
    }


async def _read_body(page) -> str:
    try:
        return (await page.locator("body").inner_text())[:10000]
    except Exception:
        return ""


def _classify_text(body: str) -> str:
    """根据页面文本返回 'verification' / 'login_failed' / 'unknown'。"""
    for kw in VERIFICATION_KEYWORDS:
        if kw in body:
            return "verification"
    for kw in LOGIN_FAILED_KEYWORDS:
        if kw.lower() in body.lower():
            return "login_failed"
    return "unknown"


async def _locator_first_visible(page, selectors: list[str], kind: str) -> object | None:
    """按优先级选择第一个可见的 locator；返回 Playwright Locator 或 None。

    ``kind`` 仅作日志用。
    """
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=2000)
            return loc
        except (PlaywrightTimeoutError, Exception):
            continue
    return None


async def _fill_login_form(page, username: str, password: str, configured_selectors) -> tuple[bool, str]:
    """定位表单并填写。返回 (filled, reason)。"""
    username_selectors = ([configured_selectors["username"]] if configured_selectors.get("username") else []) + [
        'input[name="username"]',
        'input[name="loginName"]',
        'input[name="mobile"]',
        'input[name="account"]',
        'input[type="text"]',
    ]
    password_selectors = ([configured_selectors["password"]] if configured_selectors.get("password") else []) + [
        'input[name="password"]',
        'input[type="password"]',
    ]
    submit_selectors = ([configured_selectors["submit"]] if configured_selectors.get("submit") else []) + [
        'button[type="submit"]',
        'button:has-text("登录")',
        'a:has-text("登录")',
        'input[type="submit"]',
    ]

    user_loc = await _locator_first_visible(page, username_selectors, "username")
    if user_loc is None:
        return False, "未定位到用户名输入框"
    pwd_loc = await _locator_first_visible(page, password_selectors, "password")
    if pwd_loc is None:
        return False, "未定位到密码输入框"

    try:
        await user_loc.fill("")
        await user_loc.fill(username)
        await page.wait_for_timeout(200)
        await pwd_loc.fill("")
        await pwd_loc.fill(password)
        await page.wait_for_timeout(200)
    except Exception as e:
        return False, f"填写表单失败：{type(e).__name__}: {e}"

    submit_loc = await _locator_first_visible(page, submit_selectors, "submit")
    if submit_loc is None:
        return False, "未定位到登录提交按钮"
    try:
        await submit_loc.click()
    except Exception as e:
        return False, f"提交失败：{type(e).__name__}: {e}"
    return True, ""


async def guarded_auto_login(page, config, *, max_attempts: int | None = None) -> tuple[str, str]:
    """保守型自动账号密码登录：仅一次尝试，遇任何验证挑战立即停止。

    Returns:
        ``(AuthStatus.value, reason)`` 二元组。
        可能的 status：
          - ``AUTH_OK``：登录成功，profile 已写入。
          - ``AUTH_VERIFICATION_REQUIRED``：检测到验证挑战，必须人工登录。
          - ``AUTH_LOGIN_FAILED``：用户名/密码错误。
          - ``AUTH_NETWORK_ERROR``：网络/页面加载失败。
          - ``AUTH_EXPIRED``：表单已用尽但仍未登录（应转人工）。
          - ``AUTH_UNKNOWN``：无法判定。

    设计约束：
      1. 单次尝试（max_attempts 仅作内部保护，调用方应保证 = 1）。
      2. 检测到验证码/短信/滑块/风控等任意验证挑战，立即停止，不 OCR 不绕过。
      3. 任何错误信息写入日志，但绝不输出 username/password/cookie/token/session。
    """
    # 进程级一次性保护（防止 main.py 重试循环导致累计超过 max_attempts）
    used = getattr(page, _AUTO_LOGIN_ATTEMPTS_KEY, 0)
    effective_max = int(max_attempts) if max_attempts is not None else 1
    if used >= effective_max:
        logger.warning("guarded_auto_login: 单次尝试额度已用尽（used=%d, max=%d）",
                       used, effective_max)
        return "AUTH_EXPIRED", "auto_login_max_attempts used up"
    try:
        setattr(page, _AUTO_LOGIN_ATTEMPTS_KEY, used + 1)
    except Exception:
        pass

    if not (config.username and config.password and config.login_url):
        return "AUTH_EXPIRED", "用户名/密码/login_url 未配置"

    # 1) 导航到登录页
    try:
        await page.goto(config.login_url, wait_until="domcontentloaded", timeout=30000)
    except PlaywrightTimeoutError as e:
        return "AUTH_NETWORK_ERROR", f"登录页加载超时：{e}"
    except Exception as e:
        msg = str(e) or ""
        if any(s in msg for s in ("net::ERR", "NS_ERROR", "Timeout", "Connection refused")):
            return "AUTH_NETWORK_ERROR", f"网络错误：{type(e).__name__}"
        return "AUTH_NETWORK_ERROR", f"登录页导航失败：{type(e).__name__}: {msg[:120]}"

    await page.wait_for_timeout(1000)
    body = await _read_body(page)

    # 2) 进入前先检查页面是否已经是验证墙
    kind = _classify_text(body)
    if kind == "verification":
        logger.warning("guarded_auto_login: 登录页即检测到验证挑战，停止自动登录")
        return "AUTH_VERIFICATION_REQUIRED", "登录页含验证挑战"
    if kind == "login_failed":
        # 上一次失败的提示残留；先清掉再继续
        logger.info("guarded_auto_login: 登录页残留失败提示，按正常流程继续尝试")

    # 3) 填写并提交表单
    configured = _selectors_from_config(config)
    ok, reason = await _fill_login_form(page, config.username, config.password, configured)
    if not ok:
        # 提交前失败：可能是页面结构变化 / 选择器没匹配
        body = await _read_body(page)
        kind = _classify_text(body)
        if kind == "verification":
            return "AUTH_VERIFICATION_REQUIRED", f"提交前检测到验证挑战（{reason}）"
        return "AUTH_EXPIRED", f"自动登录失败：{reason}"

    # 4) 等待页面变化
    login_url_base = (config.login_url or "").lower().rstrip("/")
    deadline_s = LOGIN_SUBMIT_WAIT_SECONDS
    last_body_kind = "unknown"
    try:
        for _ in range(deadline_s):
            await page.wait_for_timeout(1000)
            url_now = (page.url or "").lower()
            if login_url_base and not url_now.startswith(login_url_base):
                # 已离开登录页；给页面 1s 稳定
                await page.wait_for_timeout(1000)
                break
            body = await _read_body(page)
            last_body_kind = _classify_text(body)
            if last_body_kind == "verification":
                return "AUTH_VERIFICATION_REQUIRED", "提交后检测到验证挑战"
            if last_body_kind == "login_failed":
                return "AUTH_LOGIN_FAILED", "提交后页面显示登录失败"
        else:
            return "AUTH_EXPIRED", f"提交后 {deadline_s}s 内未离开登录页"
    except Exception as e:
        return "AUTH_NETWORK_ERROR", f"等待登录跳转异常：{type(e).__name__}: {e}"

    # 5) 二次校验：导航回目标页跑 check_auth（防止「已离开登录页但实际未登录」）
    if config.target_url:
        try:
            await page.goto(config.target_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
        except Exception as e:
            msg = str(e) or ""
            if any(s in msg for s in ("net::ERR", "NS_ERROR", "Timeout")):
                return "AUTH_NETWORK_ERROR", f"目标页复验失败：{type(e).__name__}"

    # 由调用方（main.py / supervisor.py）执行最终的 check_auth 以获得完整信号。
    return "AUTH_OK", "form submitted and left login page"

