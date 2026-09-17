#!/usr/bin/env python
"""SMM Authentication V2 — 一次性 profile 初始化 + Linux 人工续登入口。

Phase B 引入 persistent browser profile。Phase D 扩展此脚本支持：

模式 A — migrate：从 data/auth/storage_state.json 迁移到 persistent profile
模式 B — login：Linux 人工续登（headed on display :99 + 等待用户登录 + Auth Health Check）
模式 C — check：只读 Auth Health Check（不修改任何文件）
模式 D — lock-status：显示当前 profile lock 持有者

用法：

    # 模式 A：自动迁移（默认）
    python scripts/smm_auth_init.py

    # 模式 B：人工 headed 登录（Phase D 主入口）
    python scripts/smm_auth_init.py --login

    # 模式 C：只读检查登录态
    python scripts/smm_auth_init.py --check

    # 模式 D：查看锁状态
    python scripts/smm_auth_init.py --lock-status

    # 强制重新迁移（覆盖现有 profile）
    python scripts/smm_auth_init.py --force-migrate

安全约束：

- 不读取/输出 cookies、tokens、密码。
- profile_dir 设为 0700，权限不足则警告。
- 不修改现有 ``data/auth/storage_state.json``，仅作为只读迁移源。
- 一次性运行，不修改 cron、不修改 systemd、不修改 nginx。
- login 模式只监听 localhost；通过 SSH tunnel 远程访问。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from smm_collector import browser_v2
from smm_collector.auth_health import check_auth, AuthStatus
from smm_collector.browser_v2 import (
    DEFAULT_PROFILE_DIR,
    ensure_profile_dir,
    is_profile_initialized,
    migrate_storage_state_into_context,
    profile_dir,
)
from smm_collector.config import load_config
from smm_collector.profile_lock import (
    DEFAULT_LOCK_PATH,
    ProfileLock,
    ProfileLockError,
    resolve_lock_path,
)


# ── 模式 A：迁移 ─────────────────────────────────────────────


async def mode_migrate(config) -> bool:
    """从 data/auth/storage_state.json 迁移到 persistent profile。

    Returns:
        True 表示迁移成功；False 表示 storage_state 不存在或迁移失败。
    """
    legacy_state = config.root / "data" / "auth" / "storage_state.json"
    if not legacy_state.exists():
        print(f"[skip] 未找到旧 storage_state: {legacy_state}")
        return False

    pd = browser_v2.profile_dir(config)
    print(f"[info] 启动 persistent context，profile: {pd}")
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


# ── 模式 B：Linux 人工续登 ─────────────────────────────────


async def mode_login(config, *, headless_login_check: bool = False) -> int:
    """Phase D 主入口：在 display :99 启动 headed Chromium 让用户登录 SMM。

    流程：
    1. 获取 profile 排他锁（与 headless collector 互斥）
    2. 启动 Playwright Chromium on display :99 with persistent profile
    3. 打开 SMM 目标页（触发 SMM 自动跳登录页）
    4. 等待用户完成登录（含验证码等）
    5. 用户按 Enter 关闭浏览器
    6. 重新启动 headless persistent context（验证持久化）
    7. 执行 Auth Health Check
    9. 输出 AUTH_OK 或 AUTH_EXPIRED

    Args:
        headless_login_check: True=不实际启动 headed 浏览器，只做 headless 健康检查。

    Returns:
        exit code：0 = AUTH_OK；2 = AUTH_EXPIRED；3 = 锁冲突；4 = 异常。
    """
    pd = browser_v2.profile_dir(config)
    lock_path = resolve_lock_path(config)
    print()
    print("=" * 60)
    print("SMM Authentication Recovery — Phase D")
    print("=" * 60)
    print(f"Profile: {pd}")
    print(f"Lock:    {lock_path}")
    print(f"Display: :99 (existing TurboVNC session)")
    print(f"Target:  {config.target_url}")
    print()

    # 0) 检测现有锁（不获取）
    existing = ProfileLock(lock_path)
    if existing.acquire(blocking=False):
        # 没人占，立即释放，让 headed 阶段再获取
        existing.release()
    else:
        print(f"[ERROR] SMM browser profile currently in use.")
        print(f"[hint] 锁文件: {lock_path}")
        print(f"[hint] 检查采集器是否在运行：ps aux | grep run_daily")
        print(f"[hint] 等待其结束或删除锁后再试（删除锁前确认无 headless 运行）")
        return 3

    # 1) 启动 headed 浏览器让用户登录
    print("[step] 启动 headed Chromium（display :99）...")
    print("[hint] 浏览器将打开 SMM 登录页")
    print("[hint] 请在浏览器中完成登录（含验证码/短信/滑块）")
    print("[hint] 完成后回到此终端按 Enter 继续")
    print()

    try:
        with ProfileLock(lock_path):
            pw, context = await browser_v2.open_browser_persistent(config, headed=True)
            page = await context.new_page()
            try:
                # 先做一次 Auth Health Check（headless 时仍可，只读不写）
                try:
                    await page.goto(config.target_url, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(3000)
                except Exception as e:
                    print(f"[warn] 目标页加载异常：{type(e).__name__}: {e}")
                await asyncio.to_thread(input, ">>> 按 Enter 关闭 headed 浏览器并保存 profile...")
            finally:
                await browser_v2.close_browser_persistent(pw, context)
        print("[ok] headed 阶段完成，profile 已保存")
    except ProfileLockError as e:
        print(f"[ERROR] {e}")
        return 3

    # 2) Headless Auth Health Check（验证登录态持久化）
    print()
    print("[step] 重新启动 headless persistent context 验证登录态...")
    with ProfileLock(lock_path):
        pw, context = await browser_v2.open_browser_persistent(config, headed=False)
        page = await context.new_page()
        try:
            try:
                await page.goto(config.target_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3000)
                status, reason = await check_auth(page, config)
            except Exception as e:
                print(f"[ERROR] 页面加载失败：{type(e).__name__}: {e}")
                await browser_v2.close_browser_persistent(pw, context)
                return 4
            print()
            print(f"Auth Health Check: {status}  reason={reason}")
            if status == AuthStatus.OK:
                print()
                print("=" * 60)
                print("Persistent profile updated successfully.")
                print("AUTH_OK")
                print()
                print(f"生产 cron 启用 V2：")
                print(f"  config/settings.yaml: auth.persistent_profile: true")
                print(f"=" * 60)
                return 0
            elif status == AuthStatus.VERIFICATION_REQUIRED:
                print()
                print("=" * 60)
                print("AUTH_VERIFICATION_REQUIRED — 检测到验证挑战。")
                print("请再次运行 --login 完成登录（含验证码/短信/滑块）。")
                print("=" * 60)
                return 2
            else:
                print()
                print(f"AUTH status = {status}")
                print("请重新运行 --login 完成登录。")
                return 2
        finally:
            await browser_v2.close_browser_persistent(pw, context)


# ── 模式 C：只读 Auth Health Check ───────────────────────────


async def mode_check(config) -> int:
    """只读 Auth Health Check（不修改任何文件）。"""
    pd = browser_v2.profile_dir(config)
    if not is_profile_initialized(pd):
        print(f"[skip] profile 未初始化: {pd}")
        return 1
    lock_path = resolve_lock_path(config)
    with ProfileLock(lock_path):
        pw, context = await browser_v2.open_browser_persistent(config, headed=False)
        page = await context.new_page()
        try:
            try:
                await page.goto(config.target_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3000)
                status, reason = await check_auth(page, config)
                print(f"AUTH: {status}  reason={reason}")
                return 0 if status == AuthStatus.OK else 2
            except Exception as e:
                print(f"[ERROR] {type(e).__name__}: {e}")
                return 4
        finally:
            await browser_v2.close_browser_persistent(pw, context)


# ── 模式 D：Lock 状态 ──────────────────────────────────────


def mode_lock_status(config) -> int:
    """显示当前 profile lock 持有者（不获取锁）。"""
    lock_path = resolve_lock_path(config)
    print(f"lock_path: {lock_path}")
    if not lock_path.exists():
        print(f"status:    [unlocked] lock file does not exist")
        return 0
    # 尝试非阻塞获取
    test_lock = ProfileLock(lock_path)
    if test_lock.acquire(blocking=False):
        # 没人在用
        test_lock.release()
        print(f"status:    [unlocked]")
        return 0
    else:
        # 有人占用，读锁文件内容
        try:
            content = lock_path.read_text(encoding="utf-8", errors="ignore")
            print(f"status:    [locked by other process]")
            print(f"content:   {content.strip()}")
        except Exception as e:
            print(f"status:    [locked by other process] (read failed: {e})")
        return 3


# ── Main ────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="SMM 认证 V2 — profile 初始化 / 人工续登 / 健康检查")
    parser.add_argument("--login", action="store_true",
                        help="Phase D：在 display :99 启动 headed Chromium 完成人工登录")
    parser.add_argument("--check", action="store_true",
                        help="Phase D/D：只读 Auth Health Check（不修改任何文件）")
    parser.add_argument("--lock-status", action="store_true",
                        help="查看 profile 锁状态")
    parser.add_argument("--headed", action="store_true",
                        help="强制 headed 人工登录（不尝试迁移）")
    parser.add_argument("--force-migrate", action="store_true",
                        help="强制从 storage_state 迁移（覆盖现有 profile）")
    args = parser.parse_args()

    config = load_config(ROOT)
    pd = browser_v2.profile_dir(config)
    print(f"=== SMM Auth V2 ===")
    print(f"profile_dir:           {pd}")
    print(f"persistent_profile:    {browser_v2.persistent_profile_enabled(config)}")
    print(f"profile_lock_path:     {resolve_lock_path(config)}")
    print()

    # 模式优先级
    if args.login:
        code = asyncio.run(mode_login(config))
        sys.exit(code)

    if args.check:
        code = asyncio.run(mode_check(config))
        sys.exit(code)

    if args.lock_status:
        code = mode_lock_status(config)
        sys.exit(code)

    if args.headed:
        # 旧 headed 模式（不带 lock / health check）
        code = asyncio.run(_legacy_headed_login(config))
        sys.exit(code)

    # 默认：迁移模式
    if not args.force_migrate and is_profile_initialized(pd):
        print(f"[ok] profile 已初始化: {pd}")
        print(f"[hint] 若需重新登录：python scripts/smm_auth_init.py --login")
        print(f"[hint] 健康检查：python scripts/smm_auth_init.py --check")
        print(f"[hint] 锁状态：python scripts/smm_auth_init.py --lock-status")
        return

    print("[step] 尝试从 data/auth/storage_state.json 迁移...")
    if asyncio.run(mode_migrate(config)):
        print()
        print("[done] 迁移完成。后续采集将自动使用 persistent profile。")
        print(f"[done] 启用 V2：config/settings.yaml 设置 auth.persistent_profile=true")
        print(f"[done] 人工续登：python scripts/smm_auth_init.py --login")
        return

    print()
    print("[next] 自动迁移失败。请改用 headed/login 模式完成首次登录：")
    print("       python scripts/smm_auth_init.py --login")


async def _legacy_headed_login(config) -> int:
    """旧 headed 模式（不带 lock / health check）—— 兼容旧用法。"""
    pd = browser_v2.profile_dir(config)
    print(f"[info] 启动 headed persistent context: {pd}")
    print(f"[hint] 浏览器将打开。请在 SMM 登录页完成登录（含验证码）。")
    print(f"[hint] 完成后回到此终端按 Enter，profile 将被保存。")

    pw, context = await browser_v2.open_browser_persistent(config, headed=True)
    page = await context.new_page()
    try:
        await page.goto(config.target_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.to_thread(input, ">>> 按 Enter 关闭浏览器并保存 profile...")
    finally:
        await browser_v2.close_browser_persistent(pw, context)
    print(f"[ok] profile 已保存到: {pd}")
    print(f"[hint] 推荐改用 --login 模式（含 Auth Health Check）")
    return 0


if __name__ == "__main__":
    main()