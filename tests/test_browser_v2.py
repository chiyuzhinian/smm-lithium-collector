"""browser_v2 (Phase B Persistent Context) 单元测试。

不启动真实 Chromium，全部使用 mock 测试：
- 配置解析 (profile_dir / persistent_profile_enabled)
- profile 目录权限处理 (ensure_profile_dir / is_profile_initialized)
- storage_state → persistent context 迁移 (migrate_storage_state_into_context with mocked context)
- 不修改现有 storage_state.json 内容
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from smm_collector.browser_v2 import (
    DEFAULT_PROFILE_DIR,
    DEFAULT_PROFILE_LOCK,
    DEFAULT_BROWSER_ARGS,
    ensure_profile_dir,
    is_profile_initialized,
    migrate_storage_state_into_context,
    persistent_profile_enabled,
    profile_dir,
)


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def cfg_with_auth():
    return SimpleNamespace(
        settings={
            "browser": {"headless": True, "timeout_ms": 30000, "slow_mo_ms": 0},
            "auth": {
                "persistent_profile": True,
                "profile_dir": "/var/lib/smm-collector/browser-profile",
                "profile_lock_path": "/var/lock/smm-collector-browser.lock",
            },
        }
    )


@pytest.fixture
def cfg_legacy():
    return SimpleNamespace(
        settings={
            "browser": {"headless": True, "timeout_ms": 30000, "slow_mo_ms": 0},
            "auth": {},  # 无 auth 配置
        }
    )


@pytest.fixture
def cfg_default():
    return SimpleNamespace(
        settings={
            "browser": {"headless": True, "timeout_ms": 30000, "slow_mo_ms": 0},
            # 无 auth 段
        }
    )


# ── profile_dir / persistent_profile_enabled ───────────────


def test_profile_dir_explicit(cfg_with_auth):
    """显式配置 → 使用配置路径。"""
    p = profile_dir(cfg_with_auth)
    assert str(p) == "/var/lib/smm-collector/browser-profile"


def test_profile_dir_default(cfg_default):
    """未配置 → 回退到 DEFAULT_PROFILE_DIR。"""
    p = profile_dir(cfg_default)
    assert p == DEFAULT_PROFILE_DIR
    assert str(p) == "/var/lib/smm-collector/browser-profile"


def test_profile_dir_empty_auth(cfg_legacy):
    """auth 段存在但无 profile_dir → 回退到默认。"""
    p = profile_dir(cfg_legacy)
    assert p == DEFAULT_PROFILE_DIR


def test_persistent_profile_enabled_true(cfg_with_auth):
    assert persistent_profile_enabled(cfg_with_auth) is True


def test_persistent_profile_enabled_false(cfg_legacy):
    assert persistent_profile_enabled(cfg_legacy) is False


def test_persistent_profile_enabled_default(cfg_default):
    """未配置 → False（旧行为保持向后兼容）。"""
    assert persistent_profile_enabled(cfg_default) is False


def test_constants_sensible():
    """常量默认值合理。"""
    assert "browser-profile" in str(DEFAULT_PROFILE_DIR)
    assert "smm" in str(DEFAULT_PROFILE_LOCK).lower()
    assert "--no-first-run" in DEFAULT_BROWSER_ARGS


# ── ensure_profile_dir / is_profile_initialized ────────────


def test_ensure_profile_dir_creates(tmp_path):
    """目录不存在 → 创建并设 700。"""
    p = ensure_profile_dir(tmp_path / "new-profile")
    assert p.exists()
    assert p.is_dir()
    mode = p.stat().st_mode & 0o777
    assert mode == 0o700


def test_ensure_profile_dir_existing(tmp_path):
    """目录已存在 → 仍然 chmod 700 收紧权限。"""
    p = tmp_path / "existing"
    p.mkdir()
    p.chmod(0o755)
    ensure_profile_dir(p)
    mode = p.stat().st_mode & 0o777
    assert mode == 0o700


def test_ensure_profile_dir_returns_resolved(tmp_path):
    """返回绝对路径。"""
    p = ensure_profile_dir(tmp_path / "x")
    assert p.is_absolute()


def test_is_profile_initialized_false(tmp_path):
    """空目录 → 未初始化。"""
    assert is_profile_initialized(tmp_path) is False


def test_is_profile_initialized_true(tmp_path):
    """含 Default/ + Local State → 已初始化。"""
    default_dir = tmp_path / "Default"
    default_dir.mkdir()
    (tmp_path / "Local State").write_text("{}")
    assert is_profile_initialized(tmp_path) is True


def test_is_profile_initialized_partial(tmp_path):
    """只有 Default 无 Local State → 未初始化（防止误判）。"""
    (tmp_path / "Default").mkdir()
    assert is_profile_initialized(tmp_path) is False


# ── migrate_storage_state_into_context ─────────────────────


def _make_storage_state(cookies=None, origins=None):
    return {
        "cookies": cookies or [
            {
                "name": "sess_id",
                "value": "abc123",
                "domain": ".smm.cn",
                "path": "/",
                "expires": 1735689600,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ],
        "origins": origins or [
            {
                "origin": "https://new-energy.smm.cn",
                "localStorage": [
                    {"name": "user_token", "value": "tk_xyz"},
                    {"name": "theme", "value": "dark"},
                ],
            }
        ],
    }


def _make_context_mock():
    ctx = MagicMock()
    ctx.add_cookies = AsyncMock()
    ctx.add_init_script = AsyncMock()
    return ctx


def test_migrate_storage_state_minimal(tmp_path):
    """最小 storage_state（仅 cookies）→ 仅 add_cookies 被调用。"""
    state_file = tmp_path / "storage_state.json"
    state_file.write_text(json.dumps({"cookies": _make_storage_state()["cookies"], "origins": []}))

    ctx = _make_context_mock()
    n = asyncio.run(migrate_storage_state_into_context(ctx, state_file))

    assert n == 1  # 1 cookie
    assert ctx.add_cookies.call_count == 1
    assert ctx.add_init_script.call_count == 0


def test_migrate_storage_state_with_origins(tmp_path):
    """含 origins + localStorage → add_cookies + add_init_script 各被调用。"""
    state_file = tmp_path / "storage_state.json"
    state_file.write_text(json.dumps(_make_storage_state()))

    ctx = _make_context_mock()
    n = asyncio.run(migrate_storage_state_into_context(ctx, state_file))

    assert n == 1
    assert ctx.add_cookies.call_count == 1
    assert ctx.add_init_script.call_count == 1
    # 验证 init_script 内容
    init_script = ctx.add_init_script.call_args.args[0]
    assert "new-energy.smm.cn" in init_script
    assert "user_token" in init_script


def test_migrate_storage_state_same_site_none_keeps(tmp_path):
    """sameSite='None' 是 Playwright 合法值（HTTP SameSite=None）→ 保留。"""
    state = _make_storage_state()
    state["cookies"][0]["sameSite"] = "None"
    state_file = tmp_path / "storage_state.json"
    state_file.write_text(json.dumps(state))

    ctx = _make_context_mock()
    n = asyncio.run(migrate_storage_state_into_context(ctx, state_file))

    assert n == 1
    cookies_arg = ctx.add_cookies.call_args.args[0]
    # "None" 是合法枚举值，应保留
    assert cookies_arg[0].get("sameSite") == "None"


def test_migrate_storage_state_invalid_same_site_drops(tmp_path):
    """sameSite 不在 Playwright 枚举值（Strict/Lax/None）→ 丢弃不报错。"""
    state = _make_storage_state()
    state["cookies"][0]["sameSite"] = "garbage"
    state_file = tmp_path / "storage_state.json"
    state_file.write_text(json.dumps(state))

    ctx = _make_context_mock()
    n = asyncio.run(migrate_storage_state_into_context(ctx, state_file))

    assert n == 1
    cookies_arg = ctx.add_cookies.call_args.args[0]
    assert "sameSite" not in cookies_arg[0]


def test_migrate_storage_state_missing_file(tmp_path):
    """storage_state.json 不存在 → 抛 FileNotFoundError。"""
    ctx = _make_context_mock()
    with pytest.raises(FileNotFoundError):
        asyncio.run(migrate_storage_state_into_context(ctx, tmp_path / "no_such.json"))


def test_migrate_storage_state_empty(tmp_path):
    """空 storage_state（cookies/origins 都为空）→ 0 cookie + 0 init_script。"""
    state_file = tmp_path / "storage_state.json"
    state_file.write_text(json.dumps({"cookies": [], "origins": []}))

    ctx = _make_context_mock()
    n = asyncio.run(migrate_storage_state_into_context(ctx, state_file))

    assert n == 0
    assert ctx.add_cookies.call_count == 0
    assert ctx.add_init_script.call_count == 0


def test_migrate_storage_state_expires_negative(tmp_path):
    """Cookie expires=-1（session cookie）→ 不写入 expires 字段。"""
    state = _make_storage_state()
    state["cookies"][0]["expires"] = -1
    state_file = tmp_path / "storage_state.json"
    state_file.write_text(json.dumps(state))

    ctx = _make_context_mock()
    asyncio.run(migrate_storage_state_into_context(ctx, state_file))

    cookies_arg = ctx.add_cookies.call_args.args[0]
    assert "expires" not in cookies_arg[0]


def test_migrate_does_not_modify_source(tmp_path):
    """迁移函数不应修改源 storage_state.json（只读）。"""
    state = _make_storage_state()
    state_file = tmp_path / "storage_state.json"
    original_text = json.dumps(state)
    state_file.write_text(original_text)

    ctx = _make_context_mock()
    asyncio.run(migrate_storage_state_into_context(ctx, state_file))

    # 源文件未变
    assert state_file.read_text() == original_text


# ── 兼容性 / 旧代码不破坏 ─────────────────────────────────


def test_old_browser_module_unaffected():
    """旧 ``browser`` 模块不应被 browser_v2 修改。"""
    import importlib
    browser = importlib.import_module("smm_collector.browser")
    assert hasattr(browser, "open_browser")
    assert hasattr(browser, "close_browser")
    # browser_v2 暴露的是新接口，不污染旧接口
    assert not hasattr(browser, "open_browser_persistent")


def test_settings_yaml_has_auth_section():
    """settings.yaml 包含 auth 配置段。"""
    import yaml
    cfg_path = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
    with cfg_path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    assert "auth" in cfg
    assert "persistent_profile" in cfg["auth"]
    assert "profile_dir" in cfg["auth"]
    # 默认 False（旧行为保持）
    assert cfg["auth"]["persistent_profile"] is False