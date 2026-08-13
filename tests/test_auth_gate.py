"""端到端鉴权闸门测试：进程内启动 file_server，验证登录/权限/CSRF/穿越/故障关闭。

全部使用 tmp_path（exports/auth.db/portal 配置），静态页面用真实 static/（只读）。
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from smm_collector import ops_monitor, web_auth
from smm_collector.web_auth import AuthStore

web_auth.PBKDF2_ROUNDS = 2_000  # 测试提速

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "file_server.py"

PORTAL_YAML = """
portal:
  title: "SMM 锂电价格与回收业务数据中心"
  subtitle: "test"
auth:
  session_ttl_hours: 10
  admin_session_ttl_hours: 4
  cookie_secure: false
  lockout_max_failures: 5
  lockout_window_minutes: 10
  lockout_minutes: 10
  password_min_length: 8
health:
  cache_seconds: 30
  disk_warn_pct: 80
  disk_crit_pct: 90
  mem_warn_pct: 80
  mem_crit_pct: 90
  load_warn: 3.0
  load_crit: 4.0
  collector_timeout_minutes: 45
  freshness_check_hour: 14
admin:
  log_names:
    cron: "logs/cron.log"
    error: "logs/error_{date}.log"
  max_log_lines: 200
  log_days_back: 7
tasks: []
"""

AUTH_CFG = {"lockout_max_failures": 5, "lockout_window_minutes": 10,
            "lockout_minutes": 10, "password_min_length": 8,
            "session_ttl_hours": 10, "admin_session_ttl_hours": 4}

_mod = None


def _load_module():
    global _mod
    if _mod is None:
        spec = importlib.util.spec_from_file_location("file_server_under_test", SCRIPT)
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
    return _mod


@pytest.fixture
def env(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)          # 聚合只读 tmp，不碰真实数据
    monkeypatch.setattr(mod, "EXPORTS_ROOT", tmp_path / "exports")
    monkeypatch.setattr(mod, "DB_PATH", tmp_path / "smm.db")
    monkeypatch.setattr(mod, "STATIC_ROOT", Path(__file__).resolve().parents[1] / "static")
    (tmp_path / "exports").mkdir()
    portal_cfg = tmp_path / "portal.yaml"
    portal_cfg.write_text(PORTAL_YAML, encoding="utf-8")
    monkeypatch.setattr(mod, "PORTAL_CFG", portal_cfg)
    mod._portal_cfg_cache["mtime"] = 0.0
    mod._portal_cfg_cache["data"] = {}

    auth_db = tmp_path / "auth.db"
    monkeypatch.setattr(mod, "AUTH_DB", auth_db)
    store = AuthStore(str(auth_db), dict(AUTH_CFG))
    store.ensure_schema()
    monkeypatch.setattr(mod, "AUTH_STORE", store)

    server = mod.ReusableThreadingTCPServer(("127.0.0.1", 0), mod.DataCenterHandler)
    port = server.server_address[1]
    monkeypatch.setattr(mod, "PORT", port)  # 聚合中 web_status 自探测用
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(mod=mod, store=store, port=port, root=tmp_path)
    server.shutdown()
    server.server_close()
    ops_monitor.clear_cache()


def request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    # 中文路径需百分号编码（与浏览器行为一致）
    conn.request(method, quote(path, safe="/%?=&"), body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read().decode("utf-8", "replace")
    out = {"status": resp.status, "headers": {k.lower(): v for k, v in resp.getheaders()},
           "body": data}
    conn.close()
    return out


def login(port, username, password, next_path=None):
    payload = {"username": username, "password": password}
    if next_path:
        payload["next"] = next_path
    r = request(port, "POST", "/api/auth/login", json.dumps(payload),
                {"Content-Type": "application/json"})
    cookie = None
    for k, v in r["headers"].items():
        if k == "set-cookie":
            cookie = v.split(";")[0]
    return r, cookie


def cookie_header(cookie):
    return {"Cookie": cookie} if cookie else {}


def seed(store, name, password="pw-123456", role="user", must_change=False):
    assert store.create_user(name, password, role, must_change_password=must_change)
    return store.get_user(name)


# ── 未登录：公开路径可访问，其余全部拦截 ──────────────

class TestUnauthenticated:
    def test_public_paths(self, env):
        r = request(env.port, "GET", "/health")
        assert r["status"] == 200 and json.loads(r["body"])["status"] == "ok"
        assert request(env.port, "GET", "/login")["status"] == 200
        assert request(env.port, "GET", "/static/css/style.css")["status"] == 200
        assert request(env.port, "GET", "/static/js/api.js")["status"] == 200

    def test_pages_redirect_to_login(self, env):
        r = request(env.port, "GET", "/")
        assert r["status"] == 302 and r["headers"]["location"] == "/login?next=/"
        assert request(env.port, "GET", "/history")["status"] == 302
        assert request(env.port, "GET", "/today")["status"] == 302
        assert request(env.port, "GET", "/admin")["status"] == 302

    def test_api_returns_401_json(self, env):
        r = request(env.port, "GET", "/api/overview")
        assert r["status"] == 401 and json.loads(r["body"])["error"] == "unauthorized"

    def test_file_download_blocked(self, env):
        # 未登录无法通过猜测 URL 下载文件（HTML 请求 → 302 登录页）
        r = request(env.port, "GET", "/SMM锂电现货价格_历史汇总.xlsx")
        assert r["status"] == 302


# ── 登录流程 / 锁定 ───────────────────────────────────

class TestLoginFlow:
    def test_bad_credentials_unified_message(self, env):
        seed(env.store, "huayou")
        r, _ = login(env.port, "huayou", "wrong-pw")
        assert r["status"] == 401 and "用户名或密码错误" in r["body"]
        # 未知用户同一提示（不暴露用户是否存在）
        r2, _ = login(env.port, "ghost-user", "whatever-123")
        assert r2["status"] == 401 and "用户名或密码错误" in r2["body"]
        assert "set-cookie" not in r2["headers"]

    def test_lockout_after_five_failures(self, env):
        seed(env.store, "huayou")
        for _ in range(4):
            assert login(env.port, "huayou", "bad-pw-1")[0]["status"] == 401
        r5, _ = login(env.port, "huayou", "bad-pw-1")
        assert r5["status"] == 429 and json.loads(r5["body"])["retry_after"] > 0  # 第 5 次失败即锁定
        r, _ = login(env.port, "huayou", "pw-123456")  # 锁内正确密码也被拒
        assert r["status"] == 429 and json.loads(r["body"])["retry_after"] > 0
        assert "set-cookie" not in r["headers"]

    def test_success_sets_session_cookie(self, env):
        seed(env.store, "admin", role="admin")
        r, cookie = login(env.port, "admin", "pw-123456")
        assert r["status"] == 200 and cookie and cookie.startswith("smm_session=")
        body = json.loads(r["body"])
        assert body["role"] == "admin" and body["must_change_password"] is False
        me = request(env.port, "GET", "/api/auth/me", headers=cookie_header(cookie))
        assert json.loads(me["body"])["username"] == "admin"
        assert request(env.port, "GET", "/", headers=cookie_header(cookie))["status"] == 200

    def test_next_validated_against_open_redirect(self, env):
        seed(env.store, "admin", role="admin")
        r, _ = login(env.port, "admin", "pw-123456", next_path="//evil.com")
        assert json.loads(r["body"])["next"] is None
        r2, _ = login(env.port, "admin", "pw-123456", next_path="/today")
        assert json.loads(r2["body"])["next"] == "/today"
        r3, _ = login(env.port, "admin", "pw-123456", next_path="/api/overview")
        assert json.loads(r3["body"])["next"] is None


# ── RBAC：管理员路径服务端校验 ────────────────────────

class TestRbac:
    def test_user_cannot_reach_admin(self, env):
        seed(env.store, "huayou")
        _, cookie = login(env.port, "huayou", "pw-123456")
        h = cookie_header(cookie)
        assert request(env.port, "GET", "/admin", headers=h)["status"] == 403
        r = request(env.port, "GET", "/api/admin/overview", headers=h)
        assert r["status"] == 403 and json.loads(r["body"])["error"] == "无管理员权限"
        assert request(env.port, "GET", "/api/admin/logs?name=cron", headers=h)["status"] == 403
        # 普通用户门户正常
        assert request(env.port, "GET", "/today", headers=h)["status"] == 200

    def test_admin_reaches_dashboard(self, env):
        seed(env.store, "admin", role="admin")
        _, cookie = login(env.port, "admin", "pw-123456")
        h = cookie_header(cookie)
        assert request(env.port, "GET", "/admin", headers=h)["status"] == 200
        r = request(env.port, "GET", "/api/admin/overview", headers=h)
        assert r["status"] == 200 and "status" in json.loads(r["body"])
        r2 = request(env.port, "GET", "/api/admin/logs?name=cron&day=0&lines=50", headers=h)
        assert r2["status"] == 200 and json.loads(r2["body"])["ok"]
        r3 = request(env.port, "GET", "/api/admin/logs?name=evil", headers=h)
        assert r3["status"] == 200 and not json.loads(r3["body"])["ok"]  # 白名单拒绝
        assert request(env.port, "GET", "/api/admin/audit", headers=h)["status"] == 200


# ── 强制改密 ─────────────────────────────────────────

class TestMustChangePassword:
    def test_forced_flow(self, env):
        seed(env.store, "huayou", must_change=True)
        r, cookie = login(env.port, "huayou", "pw-123456")
        assert json.loads(r["body"])["must_change_password"] is True
        h = cookie_header(cookie)
        # 除白名单外全部拦截：页面 302 改密页，API 403 + code
        rp = request(env.port, "GET", "/", headers=h)
        assert rp["status"] == 302 and rp["headers"]["location"] == "/account"
        ra = request(env.port, "GET", "/api/overview", headers=h)
        body = json.loads(ra["body"])
        assert ra["status"] == 403 and body["code"] == "PASSWORD_CHANGE_REQUIRED"
        assert request(env.port, "GET", "/account", headers=h)["status"] == 200
        # 取 CSRF 后改密 → 旧会话全失效
        csrf = json.loads(request(env.port, "GET", "/api/auth/csrf", headers=h)["body"])["csrf_token"]
        r2 = request(env.port, "POST", "/api/auth/change-password",
                     json.dumps({"current": "pw-123456", "new": "new-pw-456"}),
                     {"Content-Type": "application/json", "X-CSRF": csrf, **h})
        assert r2["status"] == 200
        assert request(env.port, "GET", "/api/auth/me", headers=h)["status"] == 401  # 旧会话已死
        # 重新登录后不再受限
        _, cookie2 = login(env.port, "huayou", "new-pw-456")
        assert request(env.port, "GET", "/", headers=cookie_header(cookie2))["status"] == 200


# ── 登出 / CSRF ───────────────────────────────────────

class TestLogoutAndCsrf:
    def test_logout_requires_csrf_and_kills_session(self, env):
        seed(env.store, "huayou")
        _, cookie = login(env.port, "huayou", "pw-123456")
        h = cookie_header(cookie)
        r = request(env.port, "POST", "/api/auth/logout", json.dumps({}),
                    {"Content-Type": "application/json", **h})
        assert r["status"] == 403  # 无 CSRF 头
        csrf = json.loads(request(env.port, "GET", "/api/auth/csrf", headers=h)["body"])["csrf_token"]
        r2 = request(env.port, "POST", "/api/auth/logout", json.dumps({}),
                     {"Content-Type": "application/json", "X-CSRF": csrf, **h})
        assert r2["status"] == 200
        assert request(env.port, "GET", "/api/auth/me", headers=h)["status"] == 401

    def test_change_password_validation(self, env):
        seed(env.store, "huayou")
        _, cookie = login(env.port, "huayou", "pw-123456")
        h = cookie_header(cookie)
        csrf = json.loads(request(env.port, "GET", "/api/auth/csrf", headers=h)["body"])["csrf_token"]
        base = {"Content-Type": "application/json", "X-CSRF": csrf, **h}
        r1 = request(env.port, "POST", "/api/auth/change-password",
                     json.dumps({"current": "nope-nope", "new": "new-pw-456"}), base)
        assert r1["status"] == 400 and "当前密码" in r1["body"]
        r2 = request(env.port, "POST", "/api/auth/change-password",
                     json.dumps({"current": "pw-123456", "new": "short"}), base)
        assert r2["status"] == 400


# ── 路径穿越回归 / 故障关闭 ───────────────────────────

class TestTraversalAndFailClosed:
    def test_traversal_blocked_public_and_authed(self, env):
        seed(env.store, "admin", role="admin")
        _, cookie = login(env.port, "admin", "pw-123456")
        h = cookie_header(cookie)
        for path in ("/static/../.env", "/%2e%2e/.env", "/../.env", "/static/%2e%2e/config/categories_portal.yaml"):
            assert request(env.port, "GET", path)["status"] == 403
            assert request(env.port, "GET", path, headers=h)["status"] == 403

    def test_auth_db_failure_returns_503_not_open(self, env, monkeypatch):
        seed(env.store, "huayou")
        _, cookie = login(env.port, "huayou", "pw-123456")
        h = cookie_header(cookie)

        def boom(token):
            raise sqlite3.OperationalError("db down")

        monkeypatch.setattr(env.store, "lookup_session", boom)
        r = request(env.port, "GET", "/", headers=h)
        assert r["status"] == 503  # 绝不故障开放
        r2 = request(env.port, "GET", "/api/overview", headers=h)
        assert r2["status"] == 503
        assert request(env.port, "GET", "/health")["status"] == 200  # 健康检查豁免
