#!/usr/bin/env python3
# scripts/smm_cred_check.py
#
# 一次性凭据体检：在启用 auto_login_enabled 之前，先验证
# /etc/smm-collector/secrets.env 里的 username/password 是否能让
# guarded_auto_login() 走通一次。
#
# 安全设计：
#   - **临时** Playwright 浏览器 context（不写入持久 profile）
#   - **不**调用 launch_persistent_context（不影响 /var/lib/smm-collector/browser-profile）
#   - **单次** 提交尝试（与生产 auto_login 一致；max_attempts=1）
#   - **不**写入任何 auth_status.json / collector_status.json
#   - **不**触发 SMM 采集 / DB 写入
#   - 触发验证挑战立即停（与生产策略完全一致）
#   - 输出**只**显示 AuthStatus 枚举值；绝不打印 username/password/cookie 值
#
# 用法：
#   sudo .venv/bin/python scripts/smm_cred_check.py
#
# 预期结果：
#   - AUTH_OK                              → 凭据正确，可启用 auto_login_enabled
#   - AUTH_LOGIN_FAILED                    → 密码错，请重跑 set_smm_secrets.sh
#   - AUTH_VERIFICATION_REQUIRED           → 触发 SMM 风控，需改密或 headed 登录
#   - AUTH_NETWORK_ERROR                   → 网络不通，重试或检查 SMM
#   - AUTH_EXPIRED / AUTH_UNKNOWN          → 异常；查看日志
#
# 此脚本本身不含任何凭据，可入 Git。

import asyncio
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Tuple

from dotenv import load_dotenv

# 项目根（脚本位于 scripts/ 下）
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ── 读 secrets.env（不读 .env；只看 /etc/smm-collector/）───────────────
SECRETS_FILE = "/etc/smm-collector/secrets.env"
if not Path(SECRETS_FILE).exists():
    print(f"[fatal] {SECRETS_FILE} 不存在。", file=sys.stderr)
    sys.exit(2)
if not Path(SECRETS_FILE).is_file():
    print(f"[fatal] {SECRETS_FILE} 不是文件。", file=sys.stderr)
    sys.exit(2)
if os.stat(SECRETS_FILE).st_mode & 0o077:
    print(f"[fatal] {SECRETS_FILE} 权限非 0600。", file=sys.stderr)
    sys.exit(2)

load_dotenv(SECRETS_FILE, override=True)
username = os.environ.get("SMM_USERNAME") or ""
password = os.environ.get("SMM_PASSWORD") or ""
if not username or not password:
    print("[fatal] secrets.env 缺 SMM_USERNAME 或 SMM_PASSWORD。", file=sys.stderr)
    sys.exit(2)

# 长度/字符集自检（不打印值）
u_len, p_len = len(username), len(password)
u_digits_only = username.isdigit()
print(f"==> secrets.env 已读取（SMM_USERNAME 长度={u_len}, digits_only={u_digits_only}; "
      f"SMM_PASSWORD 长度={p_len}）")
print(f"==> 此脚本只提交 1 次；与生产 auto_login 完全一致。")

# ── 构造最小 config 对象 ───────────────────────────────────────────────
# 不调 load_config()，避免加载 settings.yaml / .env 时引入额外副作用。
# guarded_auto_login 只用 5 个字段：username / password / login_url / target_url / selectors。
cfg = SimpleNamespace(
    username=username,
    password=password,
    login_url="https://user.smm.cn/login",
    target_url="https://new-energy.smm.cn/new_energy/14042",
    selectors={},
)

# ── 启动临时浏览器（**非** persistent profile）──────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("smm_cred_check")

from playwright.async_api import async_playwright  # noqa: E402

from smm_collector.authentication import guarded_auto_login  # noqa: E402


async def run_check() -> Tuple[str, str]:
    """单次凭据体检。返回 (status, reason)。"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            # 临时 context：cookies 不会写入磁盘，也不会影响持久 profile
            context = await browser.new_context()
            page = await context.new_page()
            try:
                return await guarded_auto_login(page, cfg, max_attempts=1)
            finally:
                await context.close()
        finally:
            await browser.close()


async def main() -> int:
    print("==> 启动一次性凭据体检（headless，临时 context）...")
    print(f"==> 登录页: {cfg.login_url}")
    print(f"==> 目标页: {cfg.target_url}")
    try:
        status, reason = await run_check()
    except Exception as e:
        print(f"[fatal] 体检异常：{type(e).__name__}: {e}", file=sys.stderr)
        return 3

    print()
    print("=" * 60)
    print(f"AUTH_STATUS  = {status}")
    print(f"REASON       = {reason}")
    print("=" * 60)
    print()

    if status == "AUTH_OK":
        print("[ok] 凭据正确，自动登录链路可走通一次。")
        print("[next] 如确认 Stage 13，可启用 auto_login_enabled=true。")
        return 0
    if status == "AUTH_LOGIN_FAILED":
        print("[fail] 用户名或密码错误。")
        print("[fix]  请 SSH 上服务器重跑：sudo bash /root/smm-lithium-collector/scripts/set_smm_secrets.sh")
        return 4
    if status == "AUTH_VERIFICATION_REQUIRED":
        print("[fail] SMM 触发验证码/短信/滑块/风控。")
        print("[fix]  1) 登录 SMM 网页 → 修改密码 → 再试一次；")
        print("       2) 或 headed 模式人工登录（scripts/smm_auth_init.py --login）")
        return 5
    if status == "AUTH_NETWORK_ERROR":
        print("[fail] 网络错误（DNS/TLS/路由/SMM 后端 5xx）。")
        print("[fix]  检查 SMM 是否可达：curl -I https://user.smm.cn/login")
        return 6
    print(f"[unknown] 异常状态 {status}：{reason}")
    return 7


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)