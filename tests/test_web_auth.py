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
