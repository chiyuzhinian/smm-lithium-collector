"""门户账号与会话管理（SQLite + 标准库 PBKDF2，零外部依赖）。

设计要点：
- 密码只存 PBKDF2-SHA256 哈希（随机盐 + 恒定时间比较），绝不存明文
- 服务端 Session：token 仅以 SHA-256 入库（防 DB 泄露后被劫持），CSRF token 会话绑定
- 防爆破：按 (用户名, IP) 滚动窗口计数，超阈值锁定，计数持久化在 SQLite（重启不失效）
- 登录审计（login_audit）与运维事件（ops_events）共用同一 DB
- 本模块只依赖标准库；DB 路径与阈值全部由调用方注入，便于单元测试
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta

PBKDF2_ROUNDS = 600_000
SESSION_TOKEN_BYTES = 32
CSRF_TOKEN_BYTES = 32

# 全局信号量：限制并发 PBKDF2 校验，防止登录接口被用来耗尽 CPU
_verify_semaphore = threading.BoundedSemaphore(4)

# 未知用户也执行一次哈希校验，避免响应时间差异暴露用户名是否存在
_dummy_hash = None
_dummy_lock = threading.Lock()


def _now() -> datetime:
    """当前时间（模块级函数，便于测试注入时钟）。"""
    return datetime.now()


def _iso(dt: datetime) -> str:
    """ISO 秒级字符串（同格式可字典序比较）。"""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _from_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def hash_password(password: str) -> str:
    """PBKDF2-SHA256 哈希，格式 pbkdf2_sha256$<轮数>$<salt_b64>$<hash_b64>。"""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return "pbkdf2_sha256$%d$%s$%s" % (
        PBKDF2_ROUNDS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    """校验密码（恒定时间比较；轮数取自存储值，兼容参数升级）。"""
    try:
        algo, rounds_s, salt_b64, hash_b64 = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    with _verify_semaphore:
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(dk, expected)


def _dummy_verify(password: str) -> None:
    """对未知用户名也做一次 PBKDF2，等时化防用户名枚举。"""
    global _dummy_hash
    if _dummy_hash is None:
        with _dummy_lock:
            if _dummy_hash is None:
                _dummy_hash = hash_password(secrets.token_urlsafe(16))
    verify_password(password, _dummy_hash)


def token_hash(token: str) -> str:
    """会话 token 入库前哈希。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    username             TEXT NOT NULL UNIQUE,
    password_hash        TEXT NOT NULL,
    role                 TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin')),
    is_active            INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    last_login_at        TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash   TEXT NOT NULL UNIQUE,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    csrf_token   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    ip           TEXT,
    user_agent   TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
CREATE TABLE IF NOT EXISTS login_attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    username     TEXT NOT NULL,
    ip           TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    success      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_attempts_user ON login_attempts(username, attempted_at);
CREATE INDEX IF NOT EXISTS idx_attempts_ip   ON login_attempts(ip, attempted_at);
CREATE TABLE IF NOT EXISTS login_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL,
    ip         TEXT,
    user_agent TEXT,
    action     TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON login_audit(created_at);
CREATE TABLE IF NOT EXISTS ops_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    level      TEXT NOT NULL DEFAULT 'info',
    message    TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_created ON ops_events(created_at);
"""


class AuthStore:
    """账号/会话/审计存储。所有阈值来自构造参数 config（categories_portal.yaml 的 auth 段）。"""

    def __init__(self, db_path: str, config: dict | None = None):
        self.db_path = str(db_path)
        cfg = config or {}
        self.max_failures = int(cfg.get("lockout_max_failures", 5))
        self.window_minutes = int(cfg.get("lockout_window_minutes", 10))
        self.lockout_minutes = int(cfg.get("lockout_minutes", 10))
        self.password_min_length = int(cfg.get("password_min_length", 8))
        self.session_ttl_hours = float(cfg.get("session_ttl_hours", 10))
        self.admin_session_ttl_hours = float(cfg.get("admin_session_ttl_hours", 4))
        self._last_prune = _now()

    # ── 基础 ──────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def ensure_schema(self) -> None:
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    # ── 用户 ──────────────────────────────────────────

    def get_user(self, username: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def create_user(self, username: str, password: str, role: str = "user",
                    must_change_password: bool = True) -> bool:
        """创建用户；用户名已存在返回 False（幂等跳过）。"""
        now = _iso(_now())
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO users(username, password_hash, role, is_active,"
                    " must_change_password, created_at, updated_at)"
                    " VALUES(?,?,?,1,?,?,?)",
                    (username, hash_password(password), role,
                     int(bool(must_change_password)), now, now))
            return True
        except sqlite3.IntegrityError:
            return False

    def set_password(self, user_id: int, new_password: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET password_hash=?, must_change_password=0, updated_at=? WHERE id=?",
                (hash_password(new_password), _iso(_now()), user_id))

    def set_user_password_hash(self, user_id: int, password_hash: str,
                               must_change_password: bool = False) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET password_hash=?, must_change_password=?, updated_at=? WHERE id=?",
                (password_hash, int(bool(must_change_password)), _iso(_now()), user_id))

    # ── 登录与锁定 ────────────────────────────────────

    def _lock_status(self, username: str, ip: str, now: datetime) -> tuple[str, int | None]:
        """返回 ("ok", None) 或 ("locked", 剩余秒数)。"""
        since = _iso(now - timedelta(minutes=self.window_minutes))
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c, MAX(attempted_at) AS last FROM login_attempts"
                " WHERE username=? AND ip=? AND success=0 AND attempted_at>=?",
                (username, ip, since)).fetchone()
        if row["c"] >= self.max_failures and row["last"]:
            last = _from_iso(row["last"])
            if last is not None:
                remaining = self.lockout_minutes * 60 - (now - last).total_seconds()
                if remaining > 0:
                    return "locked", int(remaining + 0.999)
        return "ok", None

    def _record_attempt(self, username: str, ip: str, now: datetime, success: bool) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO login_attempts(username, ip, attempted_at, success) VALUES(?,?,?,?)",
                (username, ip, _iso(now), int(success)))

    def _reset_failures(self, username: str, ip: str) -> None:
        """登录成功后清零该 (用户名, IP) 的历史失败计数，避免误伤合法用户。"""
        with self._conn() as conn:
            conn.execute("DELETE FROM login_attempts WHERE username=? AND ip=? AND success=0",
                         (username, ip))

    def authenticate(self, username: str, password: str, ip: str,
                     user_agent: str = "") -> tuple[str, dict | None, int | None]:
        """校验凭据。返回 (status, user, retry_after_seconds)；
        status ∈ {"ok", "bad_credentials", "locked"}。"""
        now = _now()
        status, retry = self._lock_status(username, ip, now)
        if status == "locked":
            self._record_attempt(username, ip, now, False)
            self._audit("LOGIN_FAIL_LOCKED", username, ip, user_agent)
            return "locked", None, retry

        user = self.get_user(username)
        if user is None:
            _dummy_verify(password)
            self._record_attempt(username, ip, now, False)
            self._audit("LOGIN_FAIL", username, ip, user_agent)
            # 与已知用户路径一致：本次失败可能恰好触发锁定，重查一次返回剩余秒数
            status2, retry2 = self._lock_status(username, ip, _now())
            return ("locked", None, retry2) if status2 == "locked" else ("bad_credentials", None, None)

        ok = verify_password(password, user["password_hash"])
        if not ok or not user["is_active"]:
            self._record_attempt(username, ip, now, False)
            self._audit("LOGIN_FAIL", username, ip, user_agent)
            # 本次失败可能恰好触发锁定，重查一次以返回剩余等待秒数
            status2, retry2 = self._lock_status(username, ip, _now())
            return ("locked", None, retry2) if status2 == "locked" else ("bad_credentials", None, None)

        self._record_attempt(username, ip, now, True)
        self._reset_failures(username, ip)
        with self._conn() as conn:
            conn.execute("UPDATE users SET last_login_at=? WHERE id=?",
                         (_iso(now), user["id"]))
        self._audit("LOGIN_SUCCESS", username, ip, user_agent)
        self._prune_if_due(now)
        return "ok", user, None

    # ── 会话 ──────────────────────────────────────────

    def _ttl_hours_for(self, role: str) -> float:
        return self.admin_session_ttl_hours if role == "admin" else self.session_ttl_hours

    def create_session(self, user_id: int, ip: str, user_agent: str = "") -> tuple[str, str]:
        """创建会话，返回 (token, csrf_token)。token 只以哈希入库。"""
        user = self.get_user_by_id(user_id)
        role = user["role"] if user else "user"
        token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        csrf = secrets.token_urlsafe(CSRF_TOKEN_BYTES)
        now = _now()
        expires = now + timedelta(hours=self._ttl_hours_for(role))
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO sessions(token_hash, user_id, csrf_token, created_at,"
                " expires_at, last_seen_at, ip, user_agent) VALUES(?,?,?,?,?,?,?,?)",
                (token_hash(token), user_id, csrf, _iso(now), _iso(expires),
                 _iso(now), ip, (user_agent or "")[:500]))
        return token, csrf

    def lookup_session(self, token: str) -> dict | None:
        """按 token 查会话（含用户信息）；过期/停用返回 None 并清理。"""
        now = _now()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT s.id AS session_id, s.csrf_token, s.expires_at, s.last_seen_at,"
                " u.id AS user_id, u.username, u.role, u.is_active, u.must_change_password"
                " FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash=?",
                (token_hash(token),)).fetchone()
        if row is None:
            return None
        if row["expires_at"] <= _iso(now) or not row["is_active"]:
            self.delete_session(token)
            return None
        out = dict(row)
        out["user"] = {"id": out["user_id"], "username": out["username"],
                       "role": out["role"], "is_active": bool(out["is_active"]),
                       "must_change_password": bool(out["must_change_password"])}
        return out

    def touch_session(self, token: str) -> None:
        """滑动续期（节流）：60 秒内最多写一次；TTL 消耗过半才延长有效期。"""
        now = _now()
        th = token_hash(token)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT s.id, s.last_seen_at, s.expires_at, u.role FROM sessions s"
                " JOIN users u ON u.id = s.user_id WHERE s.token_hash=?", (th,)).fetchone()
        if row is None:
            return
        last = _from_iso(row["last_seen_at"]) or now
        if (now - last).total_seconds() < 60:
            return
        ttl = self._ttl_hours_for(row["role"]) * 3600
        expires = _from_iso(row["expires_at"])
        new_expires = row["expires_at"]
        if expires is not None and (expires - now).total_seconds() < ttl * 0.5:
            new_expires = _iso(now + timedelta(seconds=ttl))
        with self._conn() as conn:
            conn.execute("UPDATE sessions SET last_seen_at=?, expires_at=? WHERE id=?",
                         (_iso(now), new_expires, row["id"]))

    def delete_session(self, token: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))

    def delete_all_sessions(self, user_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def check_csrf(self, token: str, csrf_header: str | None) -> bool:
        """CSRF 校验：请求头与会话绑定 token 恒定时间比较。"""
        sess = self.lookup_session(token)
        if sess is None or not csrf_header:
            return False
        return hmac.compare_digest(sess["csrf_token"], csrf_header)

    # ── 改密 ──────────────────────────────────────────

    def change_password(self, user_id: int, old_password: str, new_password: str,
                        ip: str = "", user_agent: str = "") -> tuple[bool, str]:
        user = self.get_user_by_id(user_id)
        if user is None:
            return False, "用户不存在"
        if not verify_password(old_password, user["password_hash"]):
            return False, "当前密码不正确"
        if len(new_password) < self.password_min_length:
            return False, f"新密码长度至少 {self.password_min_length} 位"
        if verify_password(new_password, user["password_hash"]):
            return False, "新密码不能与当前密码相同"
        self.set_password(user_id, new_password)
        self.delete_all_sessions(user_id)  # 全端下线，强制重新登录
        self._audit("PASSWORD_CHANGE", user["username"], ip, user_agent)
        self.write_event("PASSWORD_CHANGE", "info", f"用户 {user['username']} 修改了密码")
        return True, "密码已修改，请重新登录"

    # ── 审计与事件 ────────────────────────────────────

    def _audit(self, action: str, username: str, ip: str = "", user_agent: str = "",
               detail: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO login_audit(username, ip, user_agent, action, detail, created_at)"
                " VALUES(?,?,?,?,?,?)",
                (username, ip, (user_agent or "")[:500], action, detail, _iso(_now())))

    def audit(self, action: str, username: str, ip: str = "", user_agent: str = "",
              detail: str = "") -> None:
        """对外审计入口（登录/登出/改密/管理员页面访问等）。"""
        self._audit(action, username, ip, user_agent, detail)

    def write_event(self, event_type: str, level: str = "info", message: str = "",
                    detail: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO ops_events(event_type, level, message, detail, created_at)"
                " VALUES(?,?,?,?,?)",
                (event_type, level, message, detail, _iso(_now())))

    def list_audit(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT username, ip, action, detail, created_at FROM login_audit"
                " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def list_events(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT event_type, level, message, detail, created_at FROM ops_events"
                " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ── 维护 ──────────────────────────────────────────

    def _prune_if_due(self, now: datetime) -> None:
        """每小时最多清理一次过期行，防表无限增长（无需额外 cron）。"""
        if (now - self._last_prune).total_seconds() < 3600:
            return
        self._last_prune = now
        self.prune(now)

    def prune(self, now: datetime | None = None) -> None:
        now = now or _now()
        with self._conn() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?",
                         (_iso(now - timedelta(days=1)),))
            conn.execute("DELETE FROM login_attempts WHERE attempted_at < ?",
                         (_iso(now - timedelta(days=2)),))
            conn.execute("DELETE FROM login_audit WHERE created_at < ?",
                         (_iso(now - timedelta(days=30)),))
            conn.execute("DELETE FROM ops_events WHERE created_at < ?",
                         (_iso(now - timedelta(days=30)),))
