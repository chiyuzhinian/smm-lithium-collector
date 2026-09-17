"""SMM 登录态健康检查 — SMM Authentication V2 Phase A。

提供结构化的 AuthStatus 判定（不只是 bool），由多个独立信号综合得出：

| 信号                 | 触发的状态                  |
|--------------------|-------------------------|
| URL 命中登录页         | AUTH_EXPIRED            |
| Body 含验证码/短信/滑块  | AUTH_VERIFICATION_REQUIRED |
| Body 含欢迎登录/密码登录  | AUTH_EXPIRED            |
| HTML 含登录墙 DOM      | AUTH_EXPIRED            |
| 价格单元格全部被锁        | AUTH_EXPIRED            |

设计原则：

1. **不绕过验证**：检测到任何形式的验证挑战（验证码/短信/滑块/安全验证）一律返回
   ``AUTH_VERIFICATION_REQUIRED``，绝不试图 OCR、滑块模拟或风控绕过。
2. **不只看 HTTP 200**：页面骨架完整不代表登录有效，必须配合价格单元格抽样。
3. **多信号融合**：任一信号触发即返回对应状态；按上述顺序检查，命中即返回。
4. **失败安全**：单信号读取失败时退化为「不命中」，不阻断其它信号；
   最终若全部失败返回 ``AUTH_UNKNOWN``（实际上不会，因为后续会抛出）。

本模块是**只读探针**，不修改页面、不修改 storage_state、不修改 profile。
不直接调用采集器之外的副作用（仅通过 ``logger`` 输出 INFO 日志）。
"""
from __future__ import annotations

import logging
from enum import Enum
from playwright.async_api import Page

from .config import AppConfig

logger = logging.getLogger("smm_collector.auth_health")


class AuthStatus(str, Enum):
    """SMM 登录态判定结果。

    使用 ``str + Enum`` 便于直接 JSON 序列化与 ops_events 写入。
    """
    OK = "AUTH_OK"
    EXPIRED = "AUTH_EXPIRED"
    VERIFICATION_REQUIRED = "AUTH_VERIFICATION_REQUIRED"
    LOGIN_FAILED = "AUTH_LOGIN_FAILED"
    NETWORK_ERROR = "AUTH_NETWORK_ERROR"
    UNKNOWN = "AUTH_UNKNOWN"


# ── 信号集合 ──────────────────────────────────────────────────────
# 验证挑战信号：只要出现就视为 AUTH_VERIFICATION_REQUIRED，必须人工处理。
VERIFICATION_SIGNALS = ("验证码", "短信验证", "滑块验证", "安全验证", "风险检测")

# 登录提示信号：登录墙或登录页出现，视为 AUTH_EXPIRED。
LOGIN_PROMPT_SIGNALS = ("欢迎登录", "密码登录")

# 登录墙 DOM 信号（2026-09-11 起 SMM 引入）。
WALL_DOM_SIGNALS = (
    'class="Unlogin_unlogin',
    'class="Unlogin_priceLocked',
    'src="/images/lock.png"',
    '/public/images/lock.png',
    'alt="登录"></span>',
)

# 登录页 URL 标识（防御性兜底，不依赖 cfg.login_url 配置）。
LOGIN_URL_HOSTS = ("user.smm.cn/login", "user.smm.cn/login.html")

# 抽样价格单元格时的「锁定」特征（HTML 子串）。
CELL_LOCK_MARKERS = ("Unlogin", "lock.png", 'alt="登录"')

# 默认抽样行数（与现有 looks_price_locked 保持一致）。
DEFAULT_SAMPLE_ROWS = 30


# ── 独立检查函数 ──────────────────────────────────────────────────


async def _check_url(page: Page, config: AppConfig) -> tuple[bool, str]:
    """检查当前 URL 是否指向登录页。

    优先级：
    1. ``cfg.login_url``（用户显式配置，可能含 query string）
    2. 内置 ``LOGIN_URL_HOSTS`` 兜底
    """
    try:
        url = page.url.lower()
    except Exception as e:
        return False, f"page.url failed: {type(e).__name__}"

    if config.login_url:
        base = config.login_url.lower().rstrip("/")
        if url.startswith(base) or base.startswith(url.rstrip("/")):
            return True, f"url matches login_url: {url[:80]}"

    for host in LOGIN_URL_HOSTS:
        if host in url:
            return True, f"url contains {host}"
    return False, ""


async def _check_body_signals(page: Page) -> tuple[set[str], str]:
    """读取页面 body 文本并匹配登录/验证关键字。

    返回 ``(信号集合, 错误描述)``。任一信号缺失即不命中。
    """
    try:
        body = (await page.locator("body").inner_text())[:10000]
    except Exception as e:
        return set(), f"body read failed: {type(e).__name__}"
    hits: set[str] = set()
    for sig in VERIFICATION_SIGNALS + LOGIN_PROMPT_SIGNALS:
        if sig in body:
            hits.add(sig)
    return hits, ""


async def _check_wall_dom(page: Page) -> tuple[bool, str]:
    """检查 HTML 中是否含登录墙 DOM 标记（Unlogin_*/lock.png/alt="登录"）。

    使用更严格的「组合信号」避免「登录」单字误判登录入口链接。
    """
    try:
        html = (await page.content())[:200000]
    except Exception as e:
        return False, f"html read failed: {type(e).__name__}"
    hits = [s for s in WALL_DOM_SIGNALS if s in html]
    if hits:
        return True, f"wall_dom hits={hits[:3]}"
    return False, ""


async def _check_price_cells(page: Page, sample_rows: int = DEFAULT_SAMPLE_ROWS) -> tuple[bool, str]:
    """抽样价格单元格是否全部显示「登录锁」图标。

    - 表格不存在 / 抽样失败：返回 ``(False, reason)``，不阻断其它检查。
    - 所有抽样单元格都被锁：返回 ``(True, reason)``，视为登录墙。
    - 任一单元格含数字：返回 ``(False, reason)``。
    """
    try:
        rows = await page.locator("table tbody tr").count()
    except Exception as e:
        return False, f"row count failed: {type(e).__name__}"
    if rows == 0:
        return False, "no rows"

    locked = 0
    checked = 0
    for i in range(min(rows, sample_rows)):
        try:
            cell_html = await page.locator("table tbody tr").nth(i).locator("td").nth(4).inner_html()
        except Exception:
            continue
        checked += 1
        if any(marker in cell_html for marker in CELL_LOCK_MARKERS):
            locked += 1
    if checked == 0:
        return False, "no cells sampled"
    if locked == checked:
        return True, f"all {checked} sampled cells locked"
    return False, f"{locked}/{checked} sampled cells locked"


# ── 综合判定 ──────────────────────────────────────────────────────


async def check_auth(
    page: Page,
    config: AppConfig,
    sample_rows: int = DEFAULT_SAMPLE_ROWS,
) -> tuple[AuthStatus, str]:
    """综合判定当前页面的 SMM 登录态。

    Args:
        page: 已加载目标页面的 Playwright Page。
        config: 项目 AppConfig。
        sample_rows: 价格单元格抽样行数，默认 30。

    Returns:
        ``(AuthStatus, reason)`` 元组：
        - ``AUTH_OK``：登录有效，可继续采集。
        - ``AUTH_EXPIRED``：登录态失效，需要重新登录（自动密码登录可尝试一次）。
        - ``AUTH_VERIFICATION_REQUIRED``：检测到验证挑战，必须人工处理。
        - ``AUTH_NETWORK_ERROR``：网络/页面加载异常。
        - ``AUTH_UNKNOWN``：无法判定（理论上不应出现）。

    Raises:
        不抛出异常；任何 Playwright 调用失败都会降级为「不命中」。
    """
    # 1) URL 命中登录页 → 失效（最快路径）
    is_login_url, reason = await _check_url(page, config)
    if is_login_url:
        logger.info("auth_check: status=EXPIRED reason=%s", reason)
        return AuthStatus.EXPIRED, reason

    # 2) Body 文本：验证挑战优先（必须人工）
    body_hits, body_err = await _check_body_signals(page)
    verification_hits = body_hits & set(VERIFICATION_SIGNALS)
    if verification_hits:
        reason = f"verification signals: {sorted(verification_hits)}"
        logger.info("auth_check: status=VERIFICATION_REQUIRED reason=%s", reason)
        return AuthStatus.VERIFICATION_REQUIRED, reason

    # 3) 登录墙 DOM（2026-09-11 起 SMM 引入的「价格隐藏锁」）
    is_wall_dom, wall_reason = await _check_wall_dom(page)
    if is_wall_dom:
        logger.info("auth_check: status=EXPIRED reason=%s", wall_reason)
        return AuthStatus.EXPIRED, wall_reason

    # 4) 价格单元格抽样（DOM 与抽样双保险）
    is_wall_cells, cells_reason = await _check_price_cells(page, sample_rows)
    if is_wall_cells:
        reason = f"wall_cells: {cells_reason}"
        logger.info("auth_check: status=EXPIRED reason=%s", reason)
        return AuthStatus.EXPIRED, reason

    # 5) 普通登录提示（无验证挑战但页面属于登录页/登录墙残影）
    login_hits = body_hits & set(LOGIN_PROMPT_SIGNALS)
    if login_hits:
        reason = f"login_prompt: {sorted(login_hits)}"
        logger.info("auth_check: status=EXPIRED reason=%s", reason)
        return AuthStatus.EXPIRED, reason

    # 全部通过
    reason = "all checks passed"
    logger.info("auth_check: status=OK reason=%s", reason)
    return AuthStatus.OK, reason


# ── 辅助：基于行的二次校验（采集结果阶段） ──────────────────────────


def check_price_wall_from_rows(rows: list[dict], threshold: float = 0.8, min_rows: int = 10) -> tuple[bool, float, str]:
    """采集结果二次校验：≥threshold 行均价为空时判定登录墙（不区分价格登录）。

    用于 ``main.py`` 在解析完成后做兜底检测 — 即使页面检测通过、解析未报错，
    仍可能因登录态变更导致均价全空。

    Args:
        rows: 已 validate 的行列表（dict 形式）。
        threshold: 空价行占比阈值，默认 0.8。
        min_rows: 最低行数门槛，低于此数不触发判定。

    Returns:
        ``(suspected, null_ratio, reason)``。
    """
    total = len(rows)
    null_avg = sum(1 for r in rows if not r.get("average_price"))
    ratio = null_avg / total if total else 0.0
    if total < min_rows:
        return False, ratio, f"rows={total} below min_rows={min_rows} ratio={ratio:.0%}"
    if ratio >= threshold:
        return True, ratio, f"null_avg={null_avg}/{total}={ratio:.0%} >= threshold={threshold:.0%}"
    return False, ratio, f"null_avg={null_avg}/{total}={ratio:.0%} ok"