"""SMM 采集浏览器 V2 — Persistent Context 模式。

SMM Authentication V2 Phase B 的核心模块。区别于 ``browser.py`` 的「每次启动新
context + 注入 storage_state」方案，本模块使用 Playwright 的
``launch_persistent_context(user_data_dir=...)``：

| 维度           | 旧（browser.py）              | 新（browser_v2.py）                |
|--------------|---------------------------|----------------------------------|
| 浏览器身份       | 每次全新 chromium 实例         | 复用持久 profile 的同一浏览器身份            |
| 会话保存         | storage_state.json（cookies/origins） | 完整 profile（cookies+localStorage+IndexedDB+cache） |
| 登录态持久化       | 弱：服务端易主动失效                | 强：模拟同一台机器上的同一浏览器                |
| 反爬/风控对抗      | 弱：设备指纹每次都新                | 强：设备/会话稳定                       |
| 接口           | ``(pw, browser, context)`` | ``(pw, context)``（context 自带浏览器） |
| Profile 并发  | 不存在（每次新 context）         | 必须 flock（Profile lock 模块负责）       |

设计原则：

1. **不绕过验证**：本模块只负责启动浏览器并加载/保存登录态，登录流程由
   ``authentication.guarded_auto_login`` 负责，遇到验证立即停止。
2. **profile 安全**：profile_dir 由 config 注入；运行期通过 ``profile_lock``
   flock 排他锁防止并发损坏。
3. **向后兼容**：旧 ``browser.open_browser()`` 不修改；本模块以独立路径提供，
   通过 ``config.auth.persistent_profile=True`` 切换。
4. **不写入敏感数据**：不在日志输出 cookies、tokens、密码。
5. **profile 路径独立**：推荐 ``/var/lib/smm-collector/browser-profile``（项目外），
   不进入 Git。

启用方法：

1. ``config/settings.yaml`` 中设置 ``auth.persistent_profile: true``。
2. ``config/settings.yaml`` 中设置 ``auth.profile_dir: /var/lib/smm-collector/browser-profile``。
3. 首次运行前执行 ``python scripts/smm_auth_init.py``（从现有 storage_state 迁移
   或提示用户通过 noVNC 完成首次登录）。
4. 之后所有采集任务直接复用同一 profile；登录态自动保持。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, BrowserContext

from .config import AppConfig

logger = logging.getLogger("smm_collector.browser_v2")

# 默认 profile 目录（项目外，独立 StateDirectory）。
DEFAULT_PROFILE_DIR = Path("/var/lib/smm-collector/browser-profile")

# 默认 browser profile 的 flock 锁路径。
DEFAULT_PROFILE_LOCK = Path("/var/lock/smm-collector-browser.lock")

# launch_persistent_context 推荐的浏览器参数。
DEFAULT_BROWSER_ARGS = (
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-blink-features=AutomationControlled",
)


# ── Profile 目录初始化 ────────────────────────────────────────────


def ensure_profile_dir(profile_dir: Path) -> Path:
    """确保 profile 目录存在并权限合规（700）。

    若目录不存在则创建；若权限过宽则收紧到 700。
    返回绝对路径。
    """
    profile_dir = profile_dir.resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        profile_dir.chmod(0o700)
    except PermissionError as e:
        logger.warning("profile_dir chmod 700 失败（%s）：%s", profile_dir, e)
    return profile_dir


def is_profile_initialized(profile_dir: Path) -> bool:
    """判定 profile 是否已初始化（含登录态痕迹）。

    Chromium 在 profile 目录下会创建 ``Default/`` 子目录和 ``Local State``。
    没有这些文件说明尚未首次启动。
    """
    profile_dir = profile_dir.resolve()
    default_dir = profile_dir / "Default"
    local_state = profile_dir / "Local State"
    return default_dir.exists() and local_state.exists()


# ── 主入口 ──────────────────────────────────────────────────────


async def open_browser_persistent(
    config: AppConfig,
    headed: bool = False,
) -> tuple[object, BrowserContext]:
    """使用 Playwright persistent context 启动 SMM 采集浏览器。

    Args:
        config: 项目 AppConfig；需含 ``auth.persistent_profile=True`` 且
            ``profile_dir`` 已配置。
        headed: 是否以有界面模式启动（默认 False）。

    Returns:
        ``(pw, context)`` 二元组。注意：persistent context 直接返回 BrowserContext，
        没有 Browser 对象；调用方调用 ``context.close()`` 即可同时关闭浏览器。

    Raises:
        RuntimeError: profile_dir 未配置或权限异常。
        ProfileLockError: profile 已被其他进程占用（由 ``profile_lock`` 抛出）。
    """
    profile_dir = _resolve_profile_dir(config)
    ensure_profile_dir(profile_dir)

    headless = config.settings["browser"]["headless"]
    slow_mo = config.settings["browser"]["slow_mo_ms"]
    timeout_ms = config.settings["browser"]["timeout_ms"]

    logger.info("启动 persistent context：profile=%s headless=%s headed=%s",
                profile_dir, headless, headed)

    pw = await async_playwright().start()
    try:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=not headed if headed else headless,
            slow_mo=slow_mo,
            args=list(DEFAULT_BROWSER_ARGS),
        )
        context.set_default_timeout(timeout_ms)
    except Exception:
        await pw.stop()
        raise
    return pw, context


async def close_browser_persistent(pw, context: BrowserContext) -> None:
    """关闭 persistent context 浏览器。

    persistent context 关闭 context 会同时关闭底层 browser；不需要单独关闭 browser。
    """
    try:
        await context.close()
    except Exception as e:
        logger.warning("关闭 persistent context 失败：%s", type(e).__name__)
    try:
        await pw.stop()
    except Exception as e:
        logger.warning("停止 playwright 失败：%s", type(e).__name__)


# ── 统一调度入口（主流程只需关心返回值） ────────────────


async def open_browser_smart(
    config: AppConfig,
    headed: bool = False,
    require_state: bool = True,
) -> tuple[object, Optional[object], BrowserContext]:
    """按配置选择 persistent 或 ephemeral 模式启动浏览器。

    **统一返回签名 ``(pw, browser, context)``**，与旧 ``browser.open_browser``
    完全一致 — main.py 不感知分支。persistent 模式下 ``browser`` 为 None。

    调用方使用 ``close_browser_smart(pw, browser, context)`` 关闭。

    Args:
        config: AppConfig；读取 ``auth.persistent_profile``。
        headed: 有界面模式（默认 False）。
        require_state: 仅对 legacy 模式生效：要求 storage_state.json 存在。

    Returns:
        ``(pw, browser, context)`` 三元组。
    """
    if persistent_profile_enabled(config):
        pw, context = await open_browser_persistent(config, headed)
        return pw, None, context

    # 走旧路径 — 延迟导入避免循环
    from .browser import open_browser as open_browser_legacy
    return await open_browser_legacy(config, headed=headed, require_state=require_state)


async def close_browser_smart(
    pw, browser: Optional[object], context: BrowserContext
) -> None:
    """关闭浏览器：persistent 模式靠 context.close()，legacy 模式靠 browser.close()。

    ``browser`` 为 None 时视为 persistent 模式；非 None 视为 legacy。
    """
    if browser is None:
        await close_browser_persistent(pw, context)
    else:
        from .browser import close_browser as close_browser_legacy
        await close_browser_legacy(pw, browser)


# ── 迁移：从 storage_state 注入到 profile ────────────────────────


async def migrate_storage_state_into_context(
    context: BrowserContext,
    storage_state_path: Path,
    target_origin: str = "https://new-energy.smm.cn",
) -> int:
    """从旧的 ``storage_state.json`` 读取 cookies 和 origins，注入到当前 context。

    persistent context 不直接读 storage_state.json；本函数作为一次性迁移入口：
    1. 解析 storage_state.json 的 ``cookies`` 列表；
    2. 通过 ``context.add_cookies()`` 注入；
    3. 解析 ``origins`` 的 ``localStorage`` 项，通过 ``context.add_init_script()``
       在首次访问对应 origin 时设置。

    Args:
        context: 已启动的 persistent context。
        storage_state_path: 旧 storage_state.json 路径。
        target_origin: 注入 origins/localStorage 时使用的源 URL（默认 new-energy.smm.cn）。

    Returns:
        注入的 cookie 数；origins 注入通过 init_script 不计数。
    """
    import json
    if not storage_state_path.exists():
        raise FileNotFoundError(f"storage_state 不存在：{storage_state_path}")

    data = json.loads(storage_state_path.read_text(encoding="utf-8"))
    cookies = data.get("cookies") or []
    origins = data.get("origins") or []

    # 1) 注入 cookies
    if cookies:
        # Playwright 需要 cookies 列表；保留必要字段
        normalized = []
        for c in cookies:
            cookie = {
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c.get("path", "/"),
            }
            if "expires" in c and c["expires"] not in (-1, None):
                cookie["expires"] = c["expires"]
            if "httpOnly" in c:
                cookie["httpOnly"] = c["httpOnly"]
            if "secure" in c:
                cookie["secure"] = c["secure"]
            if "sameSite" in c and c["sameSite"] in ("Strict", "Lax", "None"):
                cookie["sameSite"] = c["sameSite"]
            normalized.append(cookie)
        await context.add_cookies(normalized)
        logger.info("迁移 cookies：%d 条", len(normalized))

    # 2) 注入 localStorage（通过 init_script）
    if origins:
        for origin in origins:
            origin_url = origin.get("origin", "")
            local_storage = origin.get("localStorage") or []
            if not origin_url or not local_storage:
                continue
            entries = ", ".join(
                f'["{item["name"]}", {json.dumps(item["value"])}]'
                for item in local_storage
            )
            init_script = (
                f"if (location.origin === {json.dumps(origin_url)}) {{"
                f"  try {{"
                f"    Object.entries({{{entries}}}).forEach(([k,v]) => localStorage.setItem(k, v));"
                f"  }} catch (e) {{}}"
                f"}}"
            )
            await context.add_init_script(init_script)
        logger.info("迁移 origins：%d 个（含 localStorage）", len(origins))

    return len(cookies)


# ── 辅助 ────────────────────────────────────────────────────────


def _resolve_profile_dir(config: AppConfig) -> Path:
    """从 config 读取 profile_dir；未配置时回退到 DEFAULT_PROFILE_DIR。"""
    auth_cfg = config.settings.get("auth") or {}
    raw = auth_cfg.get("profile_dir") or str(DEFAULT_PROFILE_DIR)
    return Path(raw)


def profile_dir(config: AppConfig) -> Path:
    """公开的 profile_dir 解析（供测试和外部脚本使用）。"""
    return _resolve_profile_dir(config)


def persistent_profile_enabled(config: AppConfig) -> bool:
    """读取 config.auth.persistent_profile 开关。"""
    auth_cfg = config.settings.get("auth") or {}
    return bool(auth_cfg.get("persistent_profile", False))