#!/usr/bin/env python
"""SMM Authentication V2 — 一次性 profile 初始化脚本。

Phase B 引入 persistent browser profile。此脚本负责：

1. 检测 profile 是否已初始化（Default/ + Local State 都存在 → 已就绪）。
2. 若已有 ``data/auth/storage_state.json`` 且 profile 未初始化：
   - 启动 persistent context
   - 调用 ``migrate_storage_state_into_context`` 导入 cookies/origins
   - 提示用户访问目标页验证
3. 若无 storage_state 或迁移失败：
   - 启动 headed persistent context（一次性人工登录）
   - 用户在浏览器中登录 SMM
   - 完成后关闭浏览器

用法：

    # 检测并迁移（默认）
    python scripts/smm_auth_init.py

    # 强制重新迁移（覆盖现有 profile）
    python scripts/smm_auth_init.py --force-migrate

    # 强制人工登录（headed）
    python scripts/smm_auth_init.py --headed

安全约束：

- 不读取/输出 cookies、tokens、密码。
- profile_dir 设为 0700，权限不足则警告。
- 不修改现有 ``data/auth/storage_state.json``，仅作为只读迁移源。
- 一次性运行，不修改 cron、不修改 systemd。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from smm_collector import browser_v2
from smm_collector.config import load_config
from smm_collector.browser_v2 import (
    DEFAULT_PROFILE_DIR,
    ensure_profile_dir,
    is_profile_initialized,
    migrate_storage_state_into_context,
    profile_dir,
)


async def try_migrate_from_storage_state(config) -> bool:
    """尝试从 data/auth/storage_state.json 迁移到 persistent profile。

    Returns:
        True 表示迁移成功；False 表示 storage_state 不存在或迁移失败。
    """
    legacy_state = config.root / "data" / "auth" / "storage_state.json"
    if not legacy_state.exists():
        print(f"[skip] 未找到旧 storage_state: {legacy_state}")
        return False

    profile_dir = browser_v2.profile_dir(config)
    print(f"[info] 启动 persistent context，profile: {profile_dir}")
    pw, context = await browser_v2.open_browser_persistent(config, headed=False)

    try:
        n = await migrate_storage_state_into_context(context, legacy_state)
        print(f"[ok] 已迁移 {n} 条 cookies 到 persistent profile")
        print(f"[hint] 请在目标页验证登录态：{config.target_url}")
        return True
    except FileNotFoundError as e:
        print(f"[error] {e}")
        return False
    except Exception as e:
        print(f"[error] 迁移失败：{type(e).__name__}: {e}")
        return False
    finally:
        await browser_v2.close_browser_persistent(pw, context)


async def do_headed_login(config) -> None:
    """人工 headed 登录：启动浏览器让用户登录。

    提示用户按 Enter 后保存 profile 并关闭浏览器。
    """
    profile_dir = browser_v2.profile_dir(config)
    print(f"[info] 启动 headed persistent context: {profile_dir}")
    print(f"[hint] 浏览器将打开。请在 SMM 登录页完成登录（含验证码）。")
    print(f"[hint] 完成后回到此终端按 Enter，profile 将被保存。")

    pw, context = await browser_v2.open_browser_persistent(config, headed=True)
    page = await context.new_page()
    try:
        await page.goto(config.target_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.to_thread(input, ">>> 按 Enter 关闭浏览器并保存 profile...")
    finally:
        await browser_v2.close_browser_persistent(pw, context)
    print(f"[ok] profile 已保存到: {profile_dir}")


def main():
    parser = argparse.ArgumentParser(description="SMM 认证 V2 — profile 初始化")
    parser.add_argument("--headed", action="store_true",
                        help="强制 headed 人工登录（不尝试迁移）")
    parser.add_argument("--force-migrate", action="store_true",
                        help="强制从 storage_state 迁移（覆盖现有 profile）")
    args = parser.parse_args()

    config = load_config(ROOT)
    pd = browser_v2.profile_dir(config)
    print(f"=== SMM Auth V2 — Profile Init ===")
    print(f"profile_dir: {pd}")
    print(f"persistent_profile config: {browser_v2.persistent_profile_enabled(config)}")
    print()

    # 强制 headed
    if args.headed:
        asyncio.run(do_headed_login(config))
        return

    # 已初始化 + 未强制迁移 → 提示已就绪
    if not args.force_migrate and is_profile_initialized(pd):
        print(f"[ok] profile 已初始化: {pd}")
        print(f"[hint] 若需重新登录：python scripts/smm_auth_init.py --headed")
        return

    # 尝试迁移
    print("[step] 尝试从 data/auth/storage_state.json 迁移...")
    if asyncio.run(try_migrate_from_storage_state(config)):
        print()
        print("[done] 迁移完成。后续采集将自动使用 persistent profile。")
        print(f"[done] 如需关闭 V2：config/settings.yaml 设置 auth.persistent_profile=false")
        return

    # 迁移失败 → 提示 headed
    print()
    print("[next] 自动迁移失败。请改用 headed 模式完成首次登录：")
    print("       python scripts/smm_auth_init.py --headed")


if __name__ == "__main__":
    main()