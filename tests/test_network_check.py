"""network_check — 网络探测测试（Phase E1）。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from smm_collector import network_check
from smm_collector.network_check import check_network, classify_browser_network_error


@pytest.fixture
def cfg() -> SimpleNamespace:
    return SimpleNamespace(
        target_url="https://new-energy.smm.cn/new_energy/14042",
    )


# ── is_browser_network_error ──────────────────────────────


@pytest.mark.parametrize("msg,expected", [
    ("net::ERR_PROXY_CONNECTION_FAILED", True),
    ("NS_ERROR_NETWORK", True),
    ("ECONNREFUSED", True),
    ("Connection refused", True),
    ("Connection reset by peer", True),
    ("DNS_PROBE_FINISHED", True),
    ("ETIMEDOUT", True),
    ("SSL: CERTIFICATE_VERIFY_FAILED", True),
    ("TLS handshake failed", True),
    ("ReadTimeout", True),
    ("ProtocolError", True),
    ("name or service not known", True),
    ("Just a normal error", False),
    ("Login failed", False),
    ("Bad credentials", False),
])
def test_is_browser_network_error(msg, expected):
    is_net, _ = classify_browser_network_error(Exception(msg))
    assert is_net == expected


# ── check_network ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_network_success(cfg):
    """httpx 返回 200 → ok。"""
    fake_response = MagicMock()
    fake_response.status_code = 200
    with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=fake_response)):
        ok, reason = await check_network(cfg, timeout=2.0, probe_urls=("https://example.com/favicon.ico",))
    assert ok is True
    assert "status=200" in reason


@pytest.mark.asyncio
async def test_check_network_4xx_is_ok(cfg):
    """HTTP 401/403/404 → 视为可达（不算网络错）。"""
    fake_response = MagicMock()
    fake_response.status_code = 404
    with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=fake_response)):
        ok, reason = await check_network(cfg, timeout=2.0, probe_urls=("https://example.com/x",))
    assert ok is True


@pytest.mark.asyncio
async def test_check_network_5xx_is_ok(cfg):
    """HTTP 500 → 视为可达（是 SMM 后端问题，不是网络错）。"""
    fake_response = MagicMock()
    fake_response.status_code = 503
    with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=fake_response)):
        ok, reason = await check_network(cfg, timeout=2.0, probe_urls=("https://example.com/x",))
    assert ok is True


@pytest.mark.asyncio
async def test_check_network_timeout_continues_to_next(cfg):
    """第一个 URL 超时 → 尝试下一个。"""
    success_resp = MagicMock(status_code=200)
    call_count = {"n": 0}

    async def fake_get(self, url, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise httpx.ConnectTimeout("timed out")
        return success_resp

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        ok, reason = await check_network(
            cfg, timeout=2.0,
            probe_urls=("https://a.test/x", "https://b.test/y"),
        )
    assert ok is True
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_check_network_all_fail(cfg):
    """所有 URL 网络错 → ok=False。"""
    async def fake_get(self, url, **kw):
        raise httpx.ConnectError("net::ERR_NAME_NOT_RESOLVED")

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        ok, reason = await check_network(
            cfg, timeout=2.0,
            probe_urls=("https://a.test/x", "https://b.test/y"),
        )
    assert ok is False
    assert "ERR_NAME_NOT_RESOLVED" in reason or "network_error" in reason


@pytest.mark.asyncio
async def test_check_network_uses_target_url_when_provided(cfg):
    """config.target_url 应被插入为 probe 候选。"""
    fake_response = MagicMock(status_code=200)
    with patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=fake_response)) as mock:
        await check_network(cfg, timeout=2.0, probe_urls=())
    # 至少有一次调用 target_url 派生的 favicon
    urls_called = [call.args[0] for call in mock.call_args_list]
    assert any("new-energy.smm.cn" in u for u in urls_called)
