"""账号库初始化：建库建表 + 交互式创建初始账号（huayou / admin）。

- 密码经 getpass 输入（不回显、不落盘、不入配置），只存 PBKDF2-SHA256 哈希
- 幂等：已存在账号默认跳过；--reset 可强制重置
- 完成后 chown 给服务用户并收紧权限（目录 0700 / 库 0600）
- 部署时运行：.venv/bin/python scripts/init_auth.py
"""
from __future__ import annotations

import argparse
import getpass
import os
import pwd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smm_collector.web_auth import AuthStore, hash_password  # noqa: E402

DEFAULT_DB = "/var/lib/smm-fileserver/auth.db"
SERVICE_USER = "smmweb"  # 与 systemd 单元的 User= 一致（scripts/setup_systemd.sh）

# (用户名, 角色, 角色说明)
DEFAULT_USERS = [("huayou", "user", "普通用户"), ("admin", "admin", "管理员")]


def read_password(prompt: str) -> str:
    """交互输入密码；非 TTY（管道/CI）时退化为读 stdin 一行。"""
    if sys.stdin.isatty():
        return getpass.getpass(prompt)
    print(prompt, end="", flush=True)
    line = sys.stdin.readline().rstrip("\r\n")
    if line == "":  # stdin 耗尽（EOF）——避免无限循环
        sys.exit("[错误] 密码输入意外结束（stdin EOF），请交互式运行本脚本")
    return line


def prompt_password(username: str, min_length: int = 6) -> str:
    """初始密码允许 ≥6 位（如 123456）；首登强制改密时按 auth.password_min_length 校验。"""
    while True:
        p1 = read_password(f"  输入 {username} 密码: ")
        p2 = read_password(f"  再次输入 {username} 密码: ")
        if p1 != p2:
            print("  [错误] 两次输入不一致，请重试")
            continue
        if len(p1) < min_length:
            print(f"  [错误] 密码长度至少 {min_length} 位")
            continue
        return p1


def main() -> int:
    ap = argparse.ArgumentParser(description="SMM 门户账号库初始化（huayou/admin）")
    ap.add_argument("--db", default=os.getenv("FILE_SERVER_AUTH_DB", DEFAULT_DB),
                    help="账号库路径（默认 /var/lib/smm-fileserver/auth.db）")
    ap.add_argument("--reset", action="store_true",
                    help="已存在的账号也重置密码并强制首登改密")
    ap.add_argument("--no-chown", action="store_true",
                    help="不尝试 chown（无 root 或测试环境）")
    args = ap.parse_args()

    db = Path(args.db)
    try:
        db.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        sys.exit(f"[错误] 无法创建目录 {db.parent}: {e}")

    store = AuthStore(str(db), {})
    try:
        store.ensure_schema()
    except Exception as e:
        sys.exit(f"[错误] 账号库不可用 {db}: {e}")

    print(f"账号库: {db}")
    for username, role, label in DEFAULT_USERS:
        existing = store.get_user(username)
        if existing and not args.reset:
            print(f"  [跳过] {username}（{label}）已存在，如需重置请加 --reset")
            continue
        if existing:
            print(f"  [重置] {username}（{label}）")
        else:
            print(f"  [创建] {username}（{label}）")
        password = prompt_password(username, min_length=6)
        if existing:
            store.set_user_password_hash(existing["id"], hash_password(password),
                                         must_change_password=True)
        else:
            store.create_user(username, password, role, must_change_password=True)

    # 权限收紧：目录 0700 / 库 0600，属主为服务运行用户
    if not args.no_chown:
        try:
            pw = pwd.getpwnam(SERVICE_USER)
        except KeyError:
            print(f"[警告] 系统用户 {SERVICE_USER} 不存在，跳过 chown")
        else:
            try:
                db.parent.chmod(0o700)
                os.chown(db.parent, pw.pw_uid, pw.pw_gid)
                os.chown(db, pw.pw_uid, pw.pw_gid)
                db.chmod(0o600)
                for suffix in ("-wal", "-shm"):
                    p = Path(str(db) + suffix)
                    if p.exists():
                        os.chown(p, pw.pw_uid, pw.pw_gid)
                        p.chmod(0o600)
                print(f"[权限] {db.parent} 0700 / {db.name} 0600，属主 {SERVICE_USER}")
            except OSError as e:
                print(f"[警告] chown/chmod 失败（可能非 root）: {e}")

    print()
    print("账号初始化完成：")
    for username, _role, label in DEFAULT_USERS:
        print(f"  - {username}（{label}）")
    print("两个账号均为初始密码，首次登录将被强制修改。")
    print("systemd 单元需包含：StateDirectory=smm-fileserver / StateDirectoryMode=0700")
    return 0


if __name__ == "__main__":
    sys.exit(main())
