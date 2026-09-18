"""network_check — 区分 NETWORK_ERROR 与 AUTH_EXPIRED（Phase E1）。

设计要点：
- 不依赖 Chromium 启动（避免「已登录但网络不通」还要付出 30s 启动代价）
- 探测目标 URL 路径，不实际渲染页面（GET /favicon.ico 也行）
- 错误信息关键词分类：net::ERR_* / NS_ERROR / TimeoutError / Connection refused
- 区分：
  - 网络层错误（DNS / TLS / 路由）→ NETWORK_ERROR
  - HTTP 200 但页面内容 = 登录墙 → 留给 auth check 处理（不算网络错）

入口：
  - ``async check_network(config, *, timeout=15.0) -> (ok, reason)``
"""
from __future__ import annotations

import logging
from typing import Tuple

import httpx

from .config import AppConfig

logger = logging.getLogger("smm_collector.network_check")

# 网络层错误关键词（Playwright / Chromium / httpx 抛错时常见）。
NETWORK_ERROR_PATTERNS = (
    "net::ERR_", "NS_ERROR", "ECONN", "ETIMEDOUT", "ECONNRESET",
    "Connection refused", "Connection reset", "DNS",
    "Name or service not known", "Temporary failure in name resolution",
    "ssl_", "TLS", "SSL:",
    "ProtocolError", "ConnectError", "ReadTimeout",
    "TooManyRedirects", "RemoteProtocolError",
)

# 网络探测 URL 候选：SMM 任意对外可达资源即可。
PROBE_URLS_DEFAULT = (
    # favicon 通常总是返回，无重定向
    "https://new-energy.smm.cn/favicon.ico",
    "https://www.smm.cn/favicon.ico",
    "https://user.smm.cn/favicon.ico",
)


def _is_network_error(message: str) -> bool:
    msg = (message or "").lower()
    return any(p.lower() in msg for p in NETWORK_ERROR_PATTERNS)


async def check_network(
    config: AppConfig,
    *,
    timeout: float = 15.0,
    probe_urls: Tuple[str, ...] | None = None,
) -> tuple[bool, str]:
    """快速探测 SMM 是否可达（不渲染页面）。

    Returns:
        ``(ok, reason)``：``ok=True`` 表示至少一个 URL 在 timeout 内返回 2xx/3xx/4xx
        （4xx 也算 OK，401/403/404 都属于「服务器可达」）；
        ``ok=False`` 表示所有候选 URL 都网络层失败。
    """
    urls = list(probe_urls or PROBE_URLS_DEFAULT)
    if config.target_url:
        # 把 target_url 的 host 拼一个 favicon 路径（不一定存在，但能测 TLS/路由）
        from urllib.parse import urlparse
        try:
            parsed = urlparse(config.target_url)
            host_url = f"{parsed.scheme}://{parsed.netloc}"
            urls.insert(0, host_url + "/favicon.ico")
        except Exception:
            pass

    last_reason = "no probe urls"
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for url in urls:
            try:
                r = await client.get(url)
                # 4xx/5xx 都算可达（5xx 是 SMM 后端问题，不是网络错）
                logger.info("network_check: %s status=%d ok", url, r.status_code)
                return True, f"probe_ok url={url} status={r.status_code}"
            except httpx.TimeoutException as e:
                last_reason = f"timeout url={url}: {type(e).__name__}"
                logger.debug("network_check: %s → %s", url, last_reason)
            except httpx.HTTPError as e:
                msg = f"{type(e).__name__}: {e}"
                if _is_network_error(msg):
                    last_reason = f"network_error url={url}: {msg}"
                    logger.debug("network_check: %s → %s", url, last_reason)
                else:
                    last_reason = f"http_error url={url}: {msg}"
                    logger.debug("network_check: %s → %s", url, last_reason)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                if _is_network_error(msg):
                    last_reason = f"network_error url={url}: {msg}"
                else:
                    last_reason = f"unexpected url={url}: {msg}"
                logger.debug("network_check: %s → %s", url, last_reason)
    return False, last_reason


async def is_browser_network_error(exc: Exception) -> tuple[bool, str]:
    """判定一个 Playwright/httpx 异常是否属于「网络层错误」。

    用于 Supervisor 在 catch 网络层异常时不必拉起浏览器重试（仅做网络重试）。
    """
    return classify_browser_network_error(exc)


def classify_browser_network_error(exc: Exception) -> tuple[bool, str]:
    """同步版本：判定一个 Playwright/httpx 异常是否属于「网络层错误」。"""
    msg = f"{type(exc).__name__}: {exc}"
    return _is_network_error(msg), msg