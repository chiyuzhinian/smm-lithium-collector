"""邀请码初始化 CLI（需求2）：为原始管理员生成首个绑定邀请码 / 恢复通道。

用法（服务器 root 会话）：
  .venv/bin/python scripts/init_invite.py                     # 幂等：admin 无有效绑定码时生成（屏幕仅显示一次）
  .venv/bin/python scripts/init_invite.py --recover admin     # 强制：撤销现有绑定、生成新码（恢复通道）
  .venv/bin/python scripts/init_invite.py --list              # 只读列表（前缀/绑定/状态，无明文）

安全要点：
  - 码由 secrets.token_urlsafe 运行时生成，不入 Git、不入配置、不进日志
  - 数据库只存 sha256(code) 哈希与前 4 位前缀
  - 事件/审计消息只含前缀，绝不回显明文
"""
from __future__ import annotations

import argparse
import os
import pwd
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smm_collector.web_auth import AuthStore  # noqa: E402

DEFAULT_DB = "/var/lib/smm-fileserver/auth.db"
SERVICE_USER = "smmweb"  # 与 systemd 单元的 User= 一致（scripts/setup_systemd.sh）


def _chown_tighten(db: Path) -> None:
    """目录 0700 / 库 0600，属主为服务运行用户（同 init_auth.py）。"""
    try:
        pw = pwd.getpwnam(SERVICE_USER)
    except KeyError:
        print(f"[警告] 系统用户 {SERVICE_USER} 不存在，跳过 chown")
        return
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


def _new_code() -> str:
    return secrets.token_urlsafe(9)


def main() -> int:
    ap = argparse.ArgumentParser(description="SMM 门户邀请码初始化/恢复（原始管理员引导）")
    ap.add_argument("--db", default=os.getenv("FILE_SERVER_AUTH_DB", DEFAULT_DB),
                    help="账号库路径（默认 /var/lib/smm-fileserver/auth.db）")
    ap.add_argument("--recover", metavar="USER", default=None,
                    help="强制撤销该用户现有绑定并生成新码（恢复通道）")
    ap.add_argument("--list", action="store_true", help="只读列表（无明文）")
    ap.add_argument("--no-chown", action="store_true", help="不尝试 chown（无 root 或测试环境）")
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

    if args.list:
        print(f"账号库: {db}")
        for inv in store.list_invites():
            mark = "子行" if inv.get("is_child") else "父码"
            revoked = " 已撤销" if inv.get("revoked_at") else ""
            bound = f" 绑定:{inv.get('bound_username') or inv.get('bound_user_id') or '-'}"
            print(f"  #{inv['id']} [{mark}] 前缀 {inv['code_prefix']}… 用量 {inv['used_count']}"
                  f"/{inv['max_uses']}{revoked}{bound} 备注:{inv.get('note') or '-'}")
        return 0

    if args.recover:
        target_name = args.recover
        target = store.get_user(target_name)
        if target is None:
            sys.exit(f"[错误] 用户 {target_name} 不存在（--recover 需要现有用户名）")
        code = _new_code()
        info = store.rebind_invite(target["id"], code, "cli")
        if info is None:
            sys.exit("[错误] 绑定失败")
        print("=" * 56)
        print(f"[恢复] 已为 {target_name} 生成新的绑定邀请码（旧码已撤销）：")
        print(f"       邀请码: {code}")
        print("=" * 56)
        print("请立即通过安全渠道（线下/当面）告知该用户。此码仅此一次显示。")
        print("数据库仅保存哈希，无法再次查看明文；忘记请重新运行本命令。")
        if not args.no_chown:
            _chown_tighten(db)
        return 0

    # 默认模式：为 admin 生成首个绑定码（幂等——已有有效绑定则跳过）
    target = store.get_user("admin")
    if target is None:
        sys.exit("[错误] 用户 admin 不存在，请先运行 scripts/init_auth.py 初始化账号库")
    code = _new_code()
    info = store.rebind_invite(target["id"], code, "cli")
    if info is None:
        sys.exit("[错误] 绑定失败")
    print("=" * 56)
    print("[初始化] 已为 admin 生成首个绑定邀请码：")
    print(f"         邀请码: {code}")
    print("=" * 56)
    print("后续管理员可在门户 /admin 邀请码面板生成更多邀请码、撤销、")
    print("为其它用户重新绑定；本命令仅用于初始引导与恢复兜底。")
    if not args.no_chown:
        _chown_tighten(db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
