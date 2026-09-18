"""Supervisor 状态机测试（Phase E1）。

策略：
  - mock ``check_network``、``open_browser_smart``、``legacy_main.collect``
  - mock ``check_auth``、``guarded_auto_login`` 替代真实 Playwright
  - 用 tmp_path 替代 ``/var/lib/smm-collector`` 状态目录
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from smm_collector import auth_status, collector_status, supervisor
from smm_collector.auth_health import AuthStatus
from smm_collector.supervisor import (
    NETWORK_RETRY_DELAYS_S,
    SupervisorResult,
    _auth_preflight,
    _network_preflight,
    run_collection,
)


# ── Fixtures ───────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_status_dirs(tmp_path: Path, monkeypatch):
    """重定向状态文件到 tmp_path。"""
    auth_path = tmp_path / "auth_status.json"
    coll_path = tmp_path / "collector_status.json"
    monkeypatch.setattr(auth_status, "DEFAULT_PATH", auth_path)
    monkeypatch.setattr(collector_status, "DEFAULT_PATH", coll_path)
    return tmp_path


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch) -> SimpleNamespace:
    """最小化 AppConfig。"""
    c = SimpleNamespace(
        root=tmp_path,
        target_url="https://new-energy.smm.cn/new_energy/14042",
        login_url="https://user.smm.cn/login",
        username="alice",
        password="hunter2",
        selectors={},
        settings={
            "browser": {},
            "output": {
                "database_path": "data/test.db",
                "raw_dir": "data/raw",
                "export_dir": "data/exports",
                "screenshot_dir": "data/screenshots",
                "processed_dir": "data/processed",
            },
        },
    )
    monkeypatch.setattr(supervisor, "load_config", lambda: c)
    return c


# ── _network_preflight ────────────────────────────────────


@pytest.mark.asyncio
async def test_network_preflight_success():
    with patch.object(supervisor, "check_network",
                      new=AsyncMock(return_value=(True, "ok"))) as mock:
        ok, reason = await _network_preflight(MagicMock(), MagicMock())
    assert ok is True
    assert reason == "ok"
    assert mock.call_count == 1


@pytest.mark.asyncio
async def test_network_preflight_retries_then_succeeds():
    """第一次 + 第二次失败，第三次成功 → ok=True，3 次调用。"""
    mock = AsyncMock(side_effect=[(False, "timeout"), (False, "ECONN"),
                                    (True, "ok")])
    with patch.object(supervisor, "check_network", new=mock):
        ok, reason = await _network_preflight(MagicMock(), MagicMock())
    assert ok is True
    assert mock.call_count == 3


@pytest.mark.asyncio
async def test_network_preflight_exhausts_returns_false():
    """3 次都失败 → ok=False，3 次调用。"""
    mock = AsyncMock(return_value=(False, "all fail"))
    with patch.object(supervisor, "check_network", new=mock):
        ok, reason = await _network_preflight(MagicMock(), MagicMock())
    assert ok is False
    assert reason == "all fail"
    assert mock.call_count == 3


# ── _auth_preflight ──────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_preflight_ok():
    fake_page = MagicMock()
    fake_page.goto = AsyncMock()
    fake_page.wait_for_timeout = AsyncMock()
    fake_ctx = MagicMock(new_page=AsyncMock(return_value=fake_page))
    fake_pw = MagicMock()

    with patch.object(supervisor, "open_browser_smart",
                      new=AsyncMock(return_value=(fake_pw, MagicMock(), fake_ctx))), \
         patch.object(supervisor, "check_auth",
                      new=AsyncMock(return_value=(AuthStatus.OK, "passed"))), \
         patch.object(supervisor, "close_browser_smart", new=AsyncMock()):
        result = await _auth_preflight(MagicMock(), MagicMock())

    assert result.status == "success"
    assert result.auth_status == "AUTH_OK"


@pytest.mark.asyncio
async def test_auth_preflight_verification_required():
    """check_auth 返回 VERIFICATION_REQUIRED → 直接停。"""
    fake_page = MagicMock()
    fake_page.goto = AsyncMock()
    fake_page.wait_for_timeout = AsyncMock()
    fake_ctx = MagicMock(new_page=AsyncMock(return_value=fake_page))

    with patch.object(supervisor, "open_browser_smart",
                      new=AsyncMock(return_value=(MagicMock(), MagicMock(), fake_ctx))), \
         patch.object(supervisor, "check_auth",
                      new=AsyncMock(return_value=(AuthStatus.VERIFICATION_REQUIRED, "验证码"))), \
         patch.object(supervisor, "close_browser_smart", new=AsyncMock()):
        result = await _auth_preflight(MagicMock(), MagicMock())

    assert result.status == "failed"
    assert result.error_type == "AUTH_VERIFICATION_REQUIRED"


@pytest.mark.asyncio
async def test_auth_preflight_expired_triggers_auto_login():
    """EXPIRED → guarded_auto_login → AUTH_OK → 复验 OK。"""
    fake_page = MagicMock()
    fake_page.goto = AsyncMock()
    fake_page.wait_for_timeout = AsyncMock()
    fake_ctx = MagicMock(new_page=AsyncMock(return_value=fake_page))

    # 第一次 check_auth: EXPIRED；第二次（复验）: AUTH_OK
    check_auth_mock = AsyncMock(side_effect=[
        (AuthStatus.EXPIRED, "cookie gone"),
        (AuthStatus.OK, "ok"),
    ])

    with patch.object(supervisor, "open_browser_smart",
                      new=AsyncMock(return_value=(MagicMock(), MagicMock(), fake_ctx))), \
         patch.object(supervisor, "check_auth", new=check_auth_mock), \
         patch.object(supervisor, "guarded_auto_login",
                      new=AsyncMock(return_value=(AuthStatus.OK, "logged in"))), \
         patch.object(supervisor, "close_browser_smart", new=AsyncMock()):
        result = await _auth_preflight(MagicMock(), MagicMock())

    assert result.status == "success"
    assert result.auth_status == "AUTH_OK"
    assert check_auth_mock.call_count == 2  # 初始 + 复验


@pytest.mark.asyncio
async def test_auth_preflight_auto_login_fails_login_failed():
    """EXPIRED → auto_login 返回 LOGIN_FAILED → 停。"""
    fake_page = MagicMock()
    fake_page.goto = AsyncMock()
    fake_page.wait_for_timeout = AsyncMock()
    fake_ctx = MagicMock(new_page=AsyncMock(return_value=fake_page))

    with patch.object(supervisor, "open_browser_smart",
                      new=AsyncMock(return_value=(MagicMock(), MagicMock(), fake_ctx))), \
         patch.object(supervisor, "check_auth",
                      new=AsyncMock(return_value=(AuthStatus.EXPIRED, "x"))), \
         patch.object(supervisor, "guarded_auto_login",
                      new=AsyncMock(return_value=(AuthStatus.LOGIN_FAILED, "wrong password"))), \
         patch.object(supervisor, "close_browser_smart", new=AsyncMock()):
        result = await _auth_preflight(MagicMock(), MagicMock())

    assert result.status == "failed"
    assert result.error_type == "AUTH_LOGIN_FAILED"


@pytest.mark.asyncio
async def test_auth_preflight_auto_login_verification():
    """EXPIRED → auto_login 触发验证 → 停。"""
    fake_page = MagicMock()
    fake_page.goto = AsyncMock()
    fake_page.wait_for_timeout = AsyncMock()
    fake_ctx = MagicMock(new_page=AsyncMock(return_value=fake_page))

    with patch.object(supervisor, "open_browser_smart",
                      new=AsyncMock(return_value=(MagicMock(), MagicMock(), fake_ctx))), \
         patch.object(supervisor, "check_auth",
                      new=AsyncMock(return_value=(AuthStatus.EXPIRED, "x"))), \
         patch.object(supervisor, "guarded_auto_login",
                      new=AsyncMock(return_value=(AuthStatus.VERIFICATION_REQUIRED, "captcha"))), \
         patch.object(supervisor, "close_browser_smart", new=AsyncMock()):
        result = await _auth_preflight(MagicMock(), MagicMock())

    assert result.status == "failed"
    assert result.error_type == "AUTH_VERIFICATION_REQUIRED"


@pytest.mark.asyncio
async def test_auth_preflight_network_error():
    """check_auth 返回 NETWORK_ERROR → 立即停，让 network retry 接管。"""
    fake_page = MagicMock()
    fake_page.goto = AsyncMock()
    fake_page.wait_for_timeout = AsyncMock()
    fake_ctx = MagicMock(new_page=AsyncMock(return_value=fake_page))

    with patch.object(supervisor, "open_browser_smart",
                      new=AsyncMock(return_value=(MagicMock(), MagicMock(), fake_ctx))), \
         patch.object(supervisor, "check_auth",
                      new=AsyncMock(return_value=(AuthStatus.NETWORK_ERROR, "dns"))), \
         patch.object(supervisor, "close_browser_smart", new=AsyncMock()):
        result = await _auth_preflight(MagicMock(), MagicMock())

    assert result.status == "failed"
    assert result.error_type == "NETWORK_ERROR"


# ── run_collection（端到端 mock） ─────────────────────────


@pytest.mark.asyncio
async def test_run_collection_success(cfg):
    """完整流程：net ok + auth ok + collect success。"""
    with patch.object(supervisor, "_network_preflight",
                      new=AsyncMock(return_value=(True, "ok"))), \
         patch.object(supervisor, "_auth_preflight",
                      new=AsyncMock(return_value=SupervisorResult(
                              status="success", run_id="rid",
                              target_date="2026-09-17", auth_status="AUTH_OK"))), \
         patch.object(supervisor.legacy_main, "collect",
                      new=AsyncMock(return_value={
                          "status": "success",
                          "total_raw_rows": 440,
                          "total_clean_rows": 438,
                          "data_date": "2026-09-16",
                          "db_stats": {"inserted": 10, "updated": 20, "duplicate": 408},
                      })):
        result = await run_collection(date(2026, 9, 17), cfg=cfg, dry_run=False)

    assert result.status == "success"
    assert result.parsed_rows == 440
    assert result.validated_rows == 438
    assert result.inserted_rows == 10
    assert result.data_date == "2026-09-16"


@pytest.mark.asyncio
async def test_run_collection_network_failure(cfg):
    """net preflight 失败 → 立即停。"""
    with patch.object(supervisor, "_network_preflight",
                      new=AsyncMock(return_value=(False, "all probes failed"))), \
         patch.object(supervisor, "_auth_preflight",
                      new=AsyncMock()) as auth_mock, \
         patch.object(supervisor.legacy_main, "collect",
                      new=AsyncMock()) as collect_mock:
        result = await run_collection(date(2026, 9, 17), cfg=cfg, dry_run=True)

    assert result.status == "failed"
    assert result.error_type == "NETWORK_ERROR"
    assert "all probes failed" in result.error_message
    auth_mock.assert_not_called()  # auth 不应执行
    collect_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_collection_auth_failure(cfg):
    """auth 失败 → 跳过 collect。"""
    with patch.object(supervisor, "_network_preflight",
                      new=AsyncMock(return_value=(True, "ok"))), \
         patch.object(supervisor, "_auth_preflight",
                      new=AsyncMock(return_value=SupervisorResult(
                              status="failed", run_id="rid", target_date="2026-09-17",
                              auth_status="AUTH_VERIFICATION_REQUIRED",
                              error_type="AUTH_VERIFICATION_REQUIRED",
                              error_message="验证码"))), \
         patch.object(supervisor.legacy_main, "collect", new=AsyncMock()) as cm:
        result = await run_collection(date(2026, 9, 17), cfg=cfg, dry_run=True)

    assert result.status == "failed"
    assert result.error_type == "AUTH_VERIFICATION_REQUIRED"
    cm.assert_not_called()


@pytest.mark.asyncio
async def test_run_collection_zero_rows_fails(cfg):
    """main.collect 返回 total_clean_rows=0 → 失败（不写 SUCCESS）。"""
    with patch.object(supervisor, "_network_preflight",
                      new=AsyncMock(return_value=(True, "ok"))), \
         patch.object(supervisor, "_auth_preflight",
                      new=AsyncMock(return_value=SupervisorResult(
                              status="success", run_id="rid",
                              target_date="2026-09-17", auth_status="AUTH_OK"))), \
         patch.object(supervisor.legacy_main, "collect",
                      new=AsyncMock(return_value={
                          "status": "failed",
                          "total_raw_rows": 0,
                          "total_clean_rows": 0,
                          "data_date": "2026-09-17",
                          "db_stats": {"inserted": 0, "updated": 0, "duplicate": 0},
                      })):
        result = await run_collection(date(2026, 9, 17), cfg=cfg, dry_run=True)

    assert result.status == "failed"
    assert result.error_type == "ZERO_ROWS"


@pytest.mark.asyncio
async def test_run_collection_collector_exception(cfg):
    """main.collect 抛异常 → failure。"""
    with patch.object(supervisor, "_network_preflight",
                      new=AsyncMock(return_value=(True, "ok"))), \
         patch.object(supervisor, "_auth_preflight",
                      new=AsyncMock(return_value=SupervisorResult(
                              status="success", run_id="rid",
                              target_date="2026-09-17", auth_status="AUTH_OK"))), \
         patch.object(supervisor.legacy_main, "collect",
                      new=AsyncMock(side_effect=RuntimeError("parse fail"))):
        result = await run_collection(date(2026, 9, 17), cfg=cfg, dry_run=True)

    assert result.status == "failed"
    assert result.error_type == "COLLECTOR_EXCEPTION"
    assert "parse fail" in result.error_message


@pytest.mark.asyncio
async def test_run_collection_partial_success(cfg):
    """main.collect 返回 partial_success → supervisor 也视为 success+partial。"""
    with patch.object(supervisor, "_network_preflight",
                      new=AsyncMock(return_value=(True, "ok"))), \
         patch.object(supervisor, "_auth_preflight",
                      new=AsyncMock(return_value=SupervisorResult(
                              status="success", run_id="rid",
                              target_date="2026-09-17", auth_status="AUTH_OK"))), \
         patch.object(supervisor.legacy_main, "collect",
                      new=AsyncMock(return_value={
                          "status": "partial_success",
                          "total_raw_rows": 200,
                          "total_clean_rows": 200,
                          "data_date": "2026-09-16",
                          "db_stats": {"inserted": 5, "updated": 10, "duplicate": 185},
                      })):
        result = await run_collection(date(2026, 9, 17), cfg=cfg, dry_run=True)

    assert result.status == "partial_success"


# ── 状态持久化副作用 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_run_collection_writes_status_files(cfg, isolated_status_dirs):
    """成功后应写入 auth_status.json + collector_status.json。"""
    with patch.object(supervisor, "_network_preflight",
                      new=AsyncMock(return_value=(True, "ok"))), \
         patch.object(supervisor, "_auth_preflight",
                      new=AsyncMock(return_value=SupervisorResult(
                              status="success", run_id="rid",
                              target_date="2026-09-17", auth_status="AUTH_OK"))), \
         patch.object(supervisor.legacy_main, "collect",
                      new=AsyncMock(return_value={
                          "status": "success",
                          "total_raw_rows": 100,
                          "total_clean_rows": 100,
                          "data_date": "2026-09-16",
                          "db_stats": {"inserted": 5, "updated": 0, "duplicate": 95},
                      })):
        await run_collection(date(2026, 9, 17), cfg=cfg, dry_run=True)

    auth_p = isolated_status_dirs / "auth_status.json"
    coll_p = isolated_status_dirs / "collector_status.json"
    assert auth_p.exists()
    assert coll_p.exists()
    import json
    coll = json.loads(coll_p.read_text())
    assert coll["status"] == "success"
    assert coll["target_date"] == "2026-09-17"
    assert coll["auth_status"] == "AUTH_OK"


@pytest.mark.asyncio
async def test_run_collection_failure_writes_collector_status(cfg, isolated_status_dirs):
    """失败也写入 collector_status.json。"""
    with patch.object(supervisor, "_network_preflight",
                      new=AsyncMock(return_value=(False, "down"))):
        await run_collection(date(2026, 9, 17), cfg=cfg, dry_run=True)

    coll_p = isolated_status_dirs / "collector_status.json"
    assert coll_p.exists()
    import json
    coll = json.loads(coll_p.read_text())
    assert coll["status"] == "failed"
    assert coll["last_error_type"] == "NETWORK_ERROR"
    assert coll["consecutive_failures"] == 1
