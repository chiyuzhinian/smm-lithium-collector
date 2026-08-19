"""web_auth 测试：密码哈希 / 锁定窗口 / Session / CSRF / 改密 / 剪枝。

全部使用 tmp_path 与时钟注入，不触碰正式 auth.db。
测试期间把 PBKDF2 轮数调低以提速（生产默认 600k）。
"""
from __future__ import annotations

import contextlib
import sqlite3
from datetime import timedelta

import pytest

from smm_collector import web_auth
from smm_collector.web_auth import AuthStore, hash_password, verify_password

web_auth.PBKDF2_ROUNDS = 2_000

BASE_CFG = {"lockout_max_failures": 5, "lockout_window_minutes": 10,
            "lockout_minutes": 10, "password_min_length": 8,
            "session_ttl_hours": 10, "admin_session_ttl_hours": 4}


@contextlib.contextmanager
def _clock_forward(minutes: int = 0, hours: int = 0):
    """把模块时钟整体前移，用于锁定过期/会话过期场景。"""
    real = web_auth._now
    base = real() + timedelta(minutes=minutes, hours=hours)

    def fake():
        return base

    web_auth._now = fake
    try:
        yield
    finally:
        web_auth._now = real


@pytest.fixture
def store(tmp_path):
    s = AuthStore(str(tmp_path / "auth.db"), dict(BASE_CFG))
    s.ensure_schema()
    return s


class TestPasswordHash:
    def test_roundtrip(self):
        h = hash_password("secret-123")
        assert verify_password("secret-123", h)
        assert not verify_password("wrong-pw", h)

    def test_format_and_unique_salt(self):
        h1, h2 = hash_password("secret-123"), hash_password("secret-123")
        parts = h1.split("$")
        assert parts[0] == "pbkdf2_sha256" and len(parts) == 4
        assert h1 != h2  # 随机盐：同密码两次哈希不同

    def test_malformed_stored_rejected(self):
        assert not verify_password("x", "garbage")
        assert not verify_password("x", "bcrypt$1$2$3")


class TestLockout:
    def _fail_n(self, store, n, ip="1.1.1.1"):
        for _ in range(n):
            st, _, _ = store.authenticate("admin", "bad-pw", ip)
        return st

    def test_lock_after_threshold_then_recover(self, store):
        store.create_user("admin", "right-pw", "admin")
        assert self._fail_n(store, 4) == "bad_credentials"
        st, _, retry = store.authenticate("admin", "bad-pw", "1.1.1.1")  # 第 5 次 → 锁定
        assert st == "locked" and retry and retry > 0
        # 锁内正确密码也被拒（防爆破语义）
        st2, _, _ = store.authenticate("admin", "right-pw", "1.1.1.1")
        assert st2 == "locked"
        # 时钟前进超过锁定时长 → 恢复
        with _clock_forward(minutes=11):
            st3, user, _ = store.authenticate("admin", "right-pw", "1.1.1.1")
        assert st3 == "ok" and user

    def test_success_resets_counter(self, store):
        store.create_user("admin", "right-pw", "admin")
        for _ in range(4):
            store.authenticate("admin", "bad-pw", "1.1.1.1")
        st, _, _ = store.authenticate("admin", "right-pw", "1.1.1.1")
        assert st == "ok"
        # 计数器已清零：还能再失败 4 次
        assert self._fail_n(store, 4) == "bad_credentials"

    def test_unknown_user_same_semantics(self, store):
        st, user, _ = store.authenticate("nobody", "whatever", "9.9.9.9")
        assert st == "bad_credentials" and user is None

    def test_unknown_user_lockout_same_threshold(self, store):
        # 未知用户名同样在第 5 次失败时立即锁定（与已知用户路径一致）
        for _ in range(4):
            assert store.authenticate("ghost", "x-pw-0001", "3.3.3.3")[0] == "bad_credentials"
        st, _, retry = store.authenticate("ghost", "x-pw-0001", "3.3.3.3")
        assert st == "locked" and retry and retry > 0

    def test_lock_is_per_ip(self, store):
        store.create_user("admin", "right-pw", "admin")
        self._fail_n(store, 5, ip="1.1.1.1")
        assert store.authenticate("admin", "right-pw", "1.1.1.1")[0] == "locked"
        assert store.authenticate("admin", "right-pw", "2.2.2.2")[0] == "ok"


class TestSessions:
    def _user(self, store, name="huayou", role="user"):
        assert store.create_user(name, "user-pw-1", role)
        return store.get_user(name)

    def test_create_lookup_and_expire(self, store):
        u = self._user(store)
        token, csrf = store.create_session(u["id"], "1.2.3.4", "UA-x")
        sess = store.lookup_session(token)
        assert sess["user"]["username"] == "huayou" and sess["csrf_token"] == csrf
        with _clock_forward(hours=11):  # user TTL 10h
            assert store.lookup_session(token) is None

    def test_admin_ttl_shorter(self, store):
        u = self._user(store, "admin", "admin")
        token, _ = store.create_session(u["id"], "1.2.3.4")
        with _clock_forward(hours=5):  # admin TTL 4h
            assert store.lookup_session(token) is None

    def test_token_not_stored_plaintext(self, store):
        u = self._user(store)
        token, _ = store.create_session(u["id"], "1.2.3.4")
        with sqlite3.connect(store.db_path) as con:
            raw = [str(r[0]) for r in con.execute("SELECT token_hash FROM sessions")]
        assert token not in raw and token not in "".join(raw)
        assert store.lookup_session(token) is not None

    def test_logout_and_delete_all(self, store):
        u = self._user(store)
        t1, _ = store.create_session(u["id"], "1.1.1.1")
        t2, _ = store.create_session(u["id"], "1.1.1.1")
        store.delete_session(t1)
        assert store.lookup_session(t1) is None and store.lookup_session(t2) is not None
        store.delete_all_sessions(u["id"])
        assert store.lookup_session(t2) is None

    def test_inactive_user_rejected(self, store):
        u = self._user(store)
        token, _ = store.create_session(u["id"], "1.2.3.4")
        with sqlite3.connect(store.db_path) as con:
            con.execute("UPDATE users SET is_active=0 WHERE id=?", (u["id"],))
        assert store.lookup_session(token) is None

    def test_csrf_bound_to_session(self, store):
        u = self._user(store)
        token, csrf = store.create_session(u["id"], "1.2.3.4")
        assert store.check_csrf(token, csrf)
        assert not store.check_csrf(token, "forged")
        assert not store.check_csrf(token, None)
        assert not store.check_csrf("bogus-token", csrf)


class TestChangePassword:
    def test_flow(self, store):
        assert store.create_user("huayou", "old-pw-123", "user")
        u = store.get_user("huayou")
        ok, _ = store.change_password(u["id"], "old-pw-123", "new-pw-456")
        assert ok
        assert store.authenticate("huayou", "new-pw-456", "1.1.1.1")[0] == "ok"
        assert store.authenticate("huayou", "old-pw-123", "1.1.1.1")[0] == "bad_credentials"

    def test_reject_wrong_old(self, store):
        store.create_user("huayou", "old-pw-123", "user")
        u = store.get_user("huayou")
        ok, msg = store.change_password(u["id"], "nope-nope", "new-pw-456")
        assert not ok and "当前密码" in msg

    def test_reject_short_and_same(self, store):
        store.create_user("huayou", "old-pw-123", "user")
        u = store.get_user("huayou")
        ok, msg = store.change_password(u["id"], "old-pw-123", "short")
        assert not ok and "8" in msg
        ok2, _ = store.change_password(u["id"], "old-pw-123", "old-pw-123")
        assert not ok2

    def test_kills_all_sessions(self, store):
        store.create_user("huayou", "old-pw-123", "user")
        u = store.get_user("huayou")
        t1, _ = store.create_session(u["id"], "1.1.1.1")
        t2, _ = store.create_session(u["id"], "1.1.1.1")
        ok, _ = store.change_password(u["id"], "old-pw-123", "new-pw-456")
        assert ok
        assert store.lookup_session(t1) is None and store.lookup_session(t2) is None


class TestPrune:
    def test_prune_keeps_recent(self, store):
        old = web_auth._iso(web_auth._now() - timedelta(days=40))
        with sqlite3.connect(store.db_path) as con:
            con.execute("INSERT INTO login_audit(username, action, created_at) VALUES('a','LOGIN_FAIL',?)", (old,))
            con.execute("INSERT INTO ops_events(event_type, message, created_at) VALUES('X','old',?)", (old,))
        store.prune()
        with sqlite3.connect(store.db_path) as con:
            assert con.execute("SELECT COUNT(*) FROM login_audit").fetchone()[0] == 0
            assert con.execute("SELECT COUNT(*) FROM ops_events").fetchone()[0] == 0
        store.audit("LOGIN_SUCCESS", "a")
        store.prune()
        with sqlite3.connect(store.db_path) as con:
            assert con.execute("SELECT COUNT(*) FROM login_audit").fetchone()[0] == 1


class TestRegisterUser:
    def test_success_forced_plain_user(self, store):
        status, msg = store.register_user("newuser", "pw-123456")
        assert status == "ok" and "注册" in msg
        u = store.get_user("newuser")
        assert u["role"] == "user"            # 服务端强制：无任何途径注册成 admin
        assert u["is_active"] == 1 and u["must_change_password"] == 0
        assert u["is_deleted"] == 0
        assert store.authenticate("newuser", "pw-123456", "1.1.1.1")[0] == "ok"

    def test_duplicate_exact_and_case_insensitive(self, store):
        assert store.register_user("testuser", "pw-123456")[0] == "ok"
        status, msg = store.register_user("testuser", "pw-654321")
        assert status == "duplicate" and msg == "该用户名已存在，请更换用户名。"
        # 大小写不敏感：防止 testuser/Testuser 混淆
        status2, _ = store.register_user("TestUser", "pw-654321")
        assert status2 == "duplicate"

    def test_username_validation(self, store):
        assert store.register_user("ab", "pw-123456")[0] == "bad_username"          # 过短
        assert store.register_user("x" * 31, "pw-123456")[0] == "bad_username"      # 过长
        assert store.register_user("a b!", "pw-123456")[0] == "bad_username"        # 非法字符
        assert store.register_user("a@b", "pw-123456")[0] == "bad_username"
        assert store.register_user("用户甲", "pw-123456")[0] == "ok"                # 中文合法
        assert store.register_user("user_name-1", "pw-123456")[0] == "ok"           # 下划线/连字符合法

    def test_password_validation(self, store):
        assert store.register_user("pwtest1", "short12")[0] == "bad_password"       # 7 位
        assert store.register_user("pwtest2", "p" * 129)[0] == "bad_password"       # 超长
        assert store.register_user("pwtest3", "p" * 128)[0] == "ok"                 # 边界

    def test_duplicate_keeps_deleted_username_taken(self, store):
        assert store.register_user("victim", "pw-123456")[0] == "ok"
        u = store.get_user("victim")
        assert store.soft_delete_user(u["id"])
        # 软删除后用户名继续占用，不能重新注册
        assert store.register_user("victim", "pw-999999")[0] == "duplicate"


class TestListUsers:
    def test_no_password_hash_in_output(self, store):
        store.register_user("alice", "pw-123456")
        rows = store.list_users()
        assert rows and all("password_hash" not in r for r in rows)
        assert {"id", "username", "role", "is_active", "is_deleted",
                "must_change_password", "created_at", "last_login_at"} <= set(rows[0].keys())

    def test_deleted_excluded_by_default(self, store):
        store.register_user("alice", "pw-123456")
        u = store.get_user("alice")
        store.soft_delete_user(u["id"])
        assert [r["username"] for r in store.list_users()] == []
        assert [r["username"] for r in store.list_users(include_deleted=True)] == ["alice"]


class TestUserAdminOps:
    def _victim(self, store, name="victim"):
        assert store.register_user(name, "pw-123456")[0] == "ok"
        return store.get_user(name)

    def test_disable_kills_sessions_and_blocks_login(self, store):
        u = self._victim(store)
        token, _ = store.create_session(u["id"], "1.1.1.1")
        assert store.lookup_session(token) is not None
        assert store.set_user_active(u["id"], False)
        assert store.lookup_session(token) is None                                # 会话立即失效
        st, _, _ = store.authenticate("victim", "pw-123456", "1.1.1.1")
        assert st == "disabled"
        # 重新启用后可登录
        assert store.set_user_active(u["id"], True)
        assert store.authenticate("victim", "pw-123456", "2.2.2.2")[0] == "ok"

    def test_reset_password_to_default_with_forced_change(self, store):
        u = self._victim(store)
        token, _ = store.create_session(u["id"], "1.1.1.1")
        assert store.reset_user_password(u["id"])
        assert store.lookup_session(token) is None                                # 会话被清
        assert store.authenticate("victim", "pw-123456", "1.1.1.1")[0] == "bad_credentials"
        st, user, _ = store.authenticate("victim", "123456", "1.1.1.1")
        assert st == "ok" and bool(user["must_change_password"])                  # 强制首登改密
        # 改密后恢复正常
        ok, _ = store.change_password(u["id"], "123456", "new-pw-789")
        assert ok
        st2, user2, _ = store.authenticate("victim", "new-pw-789", "1.1.1.1")
        assert st2 == "ok" and not bool(user2["must_change_password"])

    def test_soft_delete_blocks_login_and_sessions(self, store):
        u = self._victim(store)
        token, _ = store.create_session(u["id"], "1.1.1.1")
        assert store.soft_delete_user(u["id"])
        assert store.lookup_session(token) is None
        assert store.authenticate("victim", "pw-123456", "1.1.1.1")[0] == "disabled"

    def test_unknown_user_id(self, store):
        assert store.set_user_active(9999, False) is False
        assert store.reset_user_password(9999) is False
        assert store.soft_delete_user(9999) is False


class TestAuthenticateDisabled:
    def test_disabled_message_only_with_correct_password(self, store):
        store.register_user("victim", "pw-123456")
        u = store.get_user("victim")
        store.set_user_active(u["id"], False)
        st, user, _ = store.authenticate("victim", "pw-123456", "1.1.1.1")
        assert st == "disabled" and user is None
        # 错误密码仍返回统一提示（不暴露账号状态，防枚举）
        st2, _, _ = store.authenticate("victim", "wrong-pw-1", "2.2.2.2")
        assert st2 == "bad_credentials"

    def test_deleted_user_same_semantics(self, store):
        store.register_user("victim", "pw-123456")
        u = store.get_user("victim")
        store.soft_delete_user(u["id"])
        assert store.authenticate("victim", "pw-123456", "1.1.1.1")[0] == "disabled"


class TestChangePasswordLockout:
    def test_wrong_old_password_counts_toward_lockout(self, store):
        store.register_user("victim", "pw-123456")
        u = store.get_user("victim")
        for _ in range(5):
            ok, _ = store.change_password(u["id"], "wrong-old", "new-pw-456", ip="1.1.1.1")
            assert not ok
        # 5 次错误原密码后，(用户名, IP) 触发登录锁定
        st, _, _ = store.authenticate("victim", "pw-123456", "1.1.1.1")
        assert st == "locked"


class TestSchemaMigration:
    def test_ensure_schema_idempotent_with_migration(self, store):
        # 两次调用不报错（幂等）
        store.ensure_schema()
        store.ensure_schema()
        with sqlite3.connect(store.db_path) as con:
            cols = [r[1] for r in con.execute("PRAGMA table_info(users)")]
        assert cols.count("is_deleted") == 1
        with sqlite3.connect(store.db_path) as con:
            idx = con.execute("SELECT name FROM sqlite_master"
                              " WHERE type='index' AND name='idx_users_username_ci'").fetchone()
        assert idx is not None

    def test_migrates_legacy_table_without_column(self, store, tmp_path):
        # 模拟旧库：建表时不带 is_deleted 列，ensure_schema 应自动补列
        legacy = str(tmp_path / "legacy.db")
        with sqlite3.connect(legacy) as con:
            con.execute("""CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin')),
                is_active INTEGER NOT NULL DEFAULT 1,
                must_change_password INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_login_at TEXT)""")
        s = AuthStore(legacy, dict(BASE_CFG))
        s.ensure_schema()
        with sqlite3.connect(legacy) as con:
            cols = [r[1] for r in con.execute("PRAGMA table_info(users)")]
        assert "is_deleted" in cols
