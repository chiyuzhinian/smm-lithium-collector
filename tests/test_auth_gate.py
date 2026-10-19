"""端到端鉴权闸门测试：进程内启动 file_server，验证登录/权限/CSRF/穿越/故障关闭。

全部使用 tmp_path（exports/auth.db/portal 配置），静态页面用真实 static/（只读）。
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import shutil
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, unquote

import pytest

from smm_collector import ops_monitor, web_auth
from smm_collector.web_auth import AuthStore

web_auth.PBKDF2_ROUNDS = 2_000  # 测试提速

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "file_server.py"

PORTAL_YAML = """
portal:
  title: "锂电价格与回收业务数据中心"
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
    # BUSINESS_CFG 模块级固化真实路径，必须一并指向 tmp（否则各测试共享真实配置的 mtime 缓存）
    monkeypatch.setattr(mod, "BUSINESS_CFG", tmp_path / "config" / "business_products.yaml")
    mod._business_cache.update({"path": None, "mtime": 0.0, "products": [], "meta": {}})
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
    # 注册节流按 IP（测试流量全部来自 127.0.0.1），每个用例清空，避免互相干扰
    monkeypatch.setattr(mod, "_register_attempts", {})

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


# ── 注册 ──────────────────────────────────────────────

def register(port, username, password, extra=None):
    payload = {"username": username, "password": password, **(extra or {})}
    return request(port, "POST", "/api/auth/register", json.dumps(payload),
                   {"Content-Type": "application/json"})


class TestRegister:
    def test_register_page_public(self, env):
        r = request(env.port, "GET", "/register")
        assert r["status"] == 200
        assert "锂电价格与回收业务数据中心" in r["body"]
        assert "华友" not in r["body"]
        assert "返回登录" in r["body"]
        # 注册页只提供用户名/密码，无任何角色选择
        assert "role" not in r["body"] and "角色" not in r["body"]

    def test_login_page_has_register_link(self, env):
        r = request(env.port, "GET", "/login")
        assert r["status"] == 200
        assert "没有账号？" in r["body"] and 'href="/register"' in r["body"]

    def test_register_then_login_full_flow(self, env):
        r = register(env.port, "newuser", "pw-123456")
        assert r["status"] == 201 and json.loads(r["body"])["ok"] is True
        # 不自动登录：注册成功后回到登录页登录
        lr, cookie = login(env.port, "newuser", "pw-123456")
        assert lr["status"] == 200
        body = json.loads(lr["body"])
        assert body["role"] == "user" and body["must_change_password"] is False
        me = json.loads(request(env.port, "GET", "/api/auth/me",
                                headers=cookie_header(cookie))["body"])
        assert me["role"] == "user" and me["must_change_password"] is False
        assert request(env.port, "GET", "/", headers=cookie_header(cookie))["status"] == 200

    def test_duplicate_username_409(self, env):
        assert register(env.port, "dupuser", "pw-123456")["status"] == 201
        r = register(env.port, "dupuser", "pw-654321")
        assert r["status"] == 409
        assert json.loads(r["body"])["error"] == "该用户名已存在，请更换用户名。"

    def test_duplicate_case_insensitive(self, env):
        assert register(env.port, "CaseUser", "pw-123456")["status"] == 201
        assert register(env.port, "caseuser", "pw-654321")["status"] == 409

    def test_role_param_ignored_server_side(self, env):
        # 伪造注册请求带 role=admin：后端必须忽略，仍创建普通用户
        r = register(env.port, "eviluser", "pw-123456", extra={"role": "admin"})
        assert r["status"] == 201
        lr, cookie = login(env.port, "eviluser", "pw-123456")
        assert json.loads(lr["body"])["role"] == "user"
        # 普通用户无法访问管理员 API
        r2 = request(env.port, "GET", "/api/admin/overview", headers=cookie_header(cookie))
        assert r2["status"] == 403

    def test_username_and_password_validation(self, env):
        assert register(env.port, "ab", "pw-123456")["status"] == 400     # 用户名过短
        assert register(env.port, "a b!", "pw-123456")["status"] == 400  # 非法字符
        assert register(env.port, "okuser", "short12")["status"] == 400  # 密码 7 位
        r = register(env.port, "okuser", "pw-123456")
        assert r["status"] == 201

    def test_register_throttle_per_ip(self, env):
        for i in range(5):
            assert register(env.port, f"bulk{i}", "pw-123456")["status"] == 201
        r = register(env.port, "bulk6", "pw-123456")
        assert r["status"] == 429  # 同 IP 每小时 5 个


# ── 管理员用户管理 ────────────────────────────────────

def admin_headers(env, cookie):
    csrf = json.loads(request(env.port, "GET", "/api/auth/csrf",
                              headers=cookie_header(cookie))["body"])["csrf_token"]
    return {"Content-Type": "application/json", "X-CSRF": csrf, **cookie_header(cookie)}


class TestAdminUserManagement:
    def test_page_and_api_rbac(self, env):
        victim = seed(env.store, "victim")
        seed(env.store, "admin", role="admin")
        _, u_cookie = login(env.port, "victim", "pw-123456")
        _, a_cookie = login(env.port, "admin", "pw-123456")
        # 普通用户：页面与 API 一律 403
        assert request(env.port, "GET", "/admin/users", headers=cookie_header(u_cookie))["status"] == 403
        assert request(env.port, "GET", "/api/admin/users", headers=cookie_header(u_cookie))["status"] == 403
        r = request(env.port, "POST", "/api/admin/users",
                    json.dumps({"id": victim["id"], "action": "disable"}),
                    {"Content-Type": "application/json", **cookie_header(u_cookie)})
        assert r["status"] == 403
        # 管理员：正常
        assert request(env.port, "GET", "/admin/users", headers=cookie_header(a_cookie))["status"] == 200
        r2 = request(env.port, "GET", "/api/admin/users", headers=cookie_header(a_cookie))
        assert r2["status"] == 200
        users = json.loads(r2["body"])["users"]
        names = [u["username"] for u in users]
        assert "victim" in names and "admin" in names
        assert all("password_hash" not in u for u in users)  # 绝不泄露密码哈希

    def test_disable_enable_lifecycle(self, env):
        victim = seed(env.store, "victim")
        seed(env.store, "admin", role="admin")
        _, v_cookie = login(env.port, "victim", "pw-123456")
        _, a_cookie = login(env.port, "admin", "pw-123456")
        h = admin_headers(env, a_cookie)
        # 停用 → 会话立即失效 + 登录提示账号已停用
        r = request(env.port, "POST", "/api/admin/users",
                    json.dumps({"id": victim["id"], "action": "disable"}), h)
        assert r["status"] == 200 and "已停用" in json.loads(r["body"])["message"]
        assert request(env.port, "GET", "/api/auth/me", headers=cookie_header(v_cookie))["status"] == 401
        lr, _ = login(env.port, "victim", "pw-123456")
        assert lr["status"] == 401 and "账号已停用" in lr["body"]
        # 启用 → 恢复登录
        r2 = request(env.port, "POST", "/api/admin/users",
                     json.dumps({"id": victim["id"], "action": "enable"}), h)
        assert r2["status"] == 200
        assert login(env.port, "victim", "pw-123456")[0]["status"] == 200

    def test_reset_password_forces_change(self, env):
        victim = seed(env.store, "victim")
        seed(env.store, "admin", role="admin")
        _, a_cookie = login(env.port, "admin", "pw-123456")
        h = admin_headers(env, a_cookie)
        r = request(env.port, "POST", "/api/admin/users",
                    json.dumps({"id": victim["id"], "action": "reset_password"}), h)
        assert r["status"] == 200 and "123456" in json.loads(r["body"])["message"]
        # 旧密码失效；123456 可登录但强制改密
        assert login(env.port, "victim", "pw-123456")[0]["status"] == 401
        lr, v_cookie = login(env.port, "victim", "123456")
        assert lr["status"] == 200 and json.loads(lr["body"])["must_change_password"] is True
        vh = cookie_header(v_cookie)
        assert request(env.port, "GET", "/", headers=vh)["status"] == 302  # 被强制去改密页
        csrf = json.loads(request(env.port, "GET", "/api/auth/csrf", headers=vh)["body"])["csrf_token"]
        r2 = request(env.port, "POST", "/api/auth/change-password",
                     json.dumps({"current": "123456", "new": "new-pw-456"}),
                     {"Content-Type": "application/json", "X-CSRF": csrf, **vh})
        assert r2["status"] == 200
        # 改密后重新登录不受限
        _, v_cookie2 = login(env.port, "victim", "new-pw-456")
        assert json.loads(
            request(env.port, "GET", "/api/auth/me", headers=cookie_header(v_cookie2))["body"]
        )["must_change_password"] is False
        assert request(env.port, "GET", "/", headers=cookie_header(v_cookie2))["status"] == 200

    def test_delete_soft(self, env):
        victim = seed(env.store, "victim")
        seed(env.store, "admin", role="admin")
        _, a_cookie = login(env.port, "admin", "pw-123456")
        h = admin_headers(env, a_cookie)
        r = request(env.port, "POST", "/api/admin/users",
                    json.dumps({"id": victim["id"], "action": "delete"}), h)
        assert r["status"] == 200
        lr, _ = login(env.port, "victim", "pw-123456")
        assert lr["status"] == 401 and "账号已停用" in lr["body"]
        users = json.loads(request(env.port, "GET", "/api/admin/users",
                                   headers=cookie_header(a_cookie))["body"])["users"]
        assert "victim" not in [u["username"] for u in users]

    def test_guard_rails(self, env):
        victim = seed(env.store, "victim")
        admin = seed(env.store, "admin", role="admin")
        _, a_cookie = login(env.port, "admin", "pw-123456")
        h = admin_headers(env, a_cookie)
        # 不能操作管理员账号
        r1 = request(env.port, "POST", "/api/admin/users",
                     json.dumps({"id": admin["id"], "action": "disable"}), h)
        assert r1["status"] == 400 and "管理员" in r1["body"]
        # 不能对自己操作（admin 目标本身也是管理员，同样拦截）
        r2 = request(env.port, "POST", "/api/admin/users",
                     json.dumps({"id": admin["id"], "action": "reset_password"}), h)
        assert r2["status"] == 400
        # 目标不存在
        r3 = request(env.port, "POST", "/api/admin/users",
                     json.dumps({"id": 9999, "action": "disable"}), h)
        assert r3["status"] == 400 and "不存在" in r3["body"]
        # 未知动作
        r4 = request(env.port, "POST", "/api/admin/users",
                     json.dumps({"id": victim["id"], "action": "make_admin"}), h)
        assert r4["status"] == 400
        # 无 CSRF 头
        r5 = request(env.port, "POST", "/api/admin/users",
                     json.dumps({"id": victim["id"], "action": "disable"}),
                     {"Content-Type": "application/json", **cookie_header(a_cookie)})
        assert r5["status"] == 403


# ── 数据质量页仅管理员 ────────────────────────────────

class TestQualityAdminOnly:
    def test_plain_user_forbidden(self, env):
        seed(env.store, "huayou")
        seed(env.store, "admin", role="admin")
        _, u_cookie = login(env.port, "huayou", "pw-123456")
        _, a_cookie = login(env.port, "admin", "pw-123456")
        # 普通用户：页面 403 页，API 403 JSON
        rp = request(env.port, "GET", "/quality", headers=cookie_header(u_cookie))
        assert rp["status"] == 403 and "无权限访问" in rp["body"]
        ra = request(env.port, "GET", "/api/quality", headers=cookie_header(u_cookie))
        assert ra["status"] == 403 and json.loads(ra["body"])["error"] == "无管理员权限"
        # 管理员：正常
        assert request(env.port, "GET", "/quality", headers=cookie_header(a_cookie))["status"] == 200
        assert request(env.port, "GET", "/api/quality", headers=cookie_header(a_cookie))["status"] == 200

    def test_unauthenticated_redirects(self, env):
        r = request(env.port, "GET", "/quality")
        assert r["status"] == 302 and r["headers"]["location"].startswith("/login")


# ── 品牌名与数据来源字符串 ─────────────────────────────

class TestBrandStrings:
    def test_login_page_brand(self, env):
        r = request(env.port, "GET", "/login")
        assert "锂电价格与回收业务数据中心" in r["body"]
        assert "SMM 锂电价格与回收业务数据中心" not in r["body"]
        assert "华友" not in r["body"]
        # 登录页数据来源说明已按业务要求删除（2026-10 需求1）；页脚保留（test_nav_js_brand_and_data_source）
        assert "数据来源：SMM 上海有色网公开报价" not in r["body"]

    def test_overview_banner_title(self, env):
        seed(env.store, "huayou")
        _, cookie = login(env.port, "huayou", "pw-123456")
        r = request(env.port, "GET", "/api/overview", headers=cookie_header(cookie))
        assert r["status"] == 200
        assert json.loads(r["body"])["banner"]["title"] == "锂电价格与回收业务数据中心"

    def test_nav_js_brand_and_no_footer(self, env):
        r = request(env.port, "GET", "/static/js/nav.js")
        assert "锂电价格与回收业务数据中心" in r["body"]
        assert "SMM 锂电价格与回收业务数据中心" not in r["body"]
        # R2：全站页脚说明已移除，nav.js 不再注入任何页脚文字
        assert "数据来源：SMM 上海有色网公开报价" not in r["body"]
        assert "涨红跌绿" not in r["body"]

    def test_home_page_brand(self, env):
        seed(env.store, "huayou")
        _, cookie = login(env.port, "huayou", "pw-123456")
        r = request(env.port, "GET", "/", headers=cookie_header(cookie))
        assert r["status"] == 200
        assert "锂电价格与回收业务数据中心" in r["body"]
        assert "SMM 锂电价格与回收业务数据中心" not in r["body"]


# ── 历史价格下载（需求4） ─────────────────────────────

def _ensure_price_table(root):
    con = sqlite3.connect(root / "smm.db")
    con.execute("""CREATE TABLE IF NOT EXISTS lithium_spot_prices (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source TEXT, market TEXT, category TEXT, product_name TEXT, specification TEXT DEFAULT '',
      min_price TEXT, max_price TEXT, average_price TEXT, change_value TEXT, unit TEXT DEFAULT '',
      price_date TEXT, collected_at TEXT, source_url TEXT, collection_method TEXT, raw_text TEXT,
      extra_fields TEXT, record_hash TEXT, validation_status TEXT, validation_message TEXT,
      created_at TEXT, updated_at TEXT)""")
    con.commit()
    con.close()


class TestHistoryDownload:
    def test_unauthenticated_401(self, env):
        r = request(env.port, "GET", "/api/portal/history/download")
        assert r["status"] == 401

    def test_download_200_xlsx_and_filename_window(self, env):
        """登录后下载 200：xlsx 魔数 + Content-Disposition 文件名回显请求窗口。"""
        _ensure_price_table(env.root)
        seed(env.store, "huayou")
        _, cookie = login(env.port, "huayou", "pw-123456")
        r = request(env.port, "GET",
                    "/api/portal/history/download?from=2026-07-20&to=2026-08-31",
                    headers=cookie_header(cookie))
        assert r["status"] == 200
        assert r["headers"]["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        assert unquote(r["headers"]["content-disposition"]).endswith(
            "历史价格_2026-07-20_2026-08-31.xlsx")
        # body 经 utf-8+replace 解码；xlsx 魔数 PK\x03\x04 前 4 字节为 ASCII/控制字符不受影响
        assert r["body"][:4] == "PK\x03\x04"

    def test_default_window_no_params_ok(self, env):
        """无参数下载也 200（from 钳 history_from、to=至今，全量 40 业务品种口径）。"""
        _ensure_price_table(env.root)
        seed(env.store, "huayou")
        _, cookie = login(env.port, "huayou", "pw-123456")
        r = request(env.port, "GET", "/api/portal/history/download",
                    headers=cookie_header(cookie))
        assert r["status"] == 200
        assert r["headers"]["content-disposition"].startswith("attachment")


# ── 月报默认产品清单（需求5） ──────────────────────────

class TestMonthlyDefaultsApi:
    @staticmethod
    def _seed_config(env):
        """把真实 config 目录复制进 tmp PROJECT_ROOT（id 白名单校验依赖 business_products.yaml）。"""
        dst = env.root / "config"
        if not dst.exists():
            shutil.copytree(Path(__file__).resolve().parents[1] / "config", dst)

    def test_get_requires_login(self, env):
        r = request(env.port, "GET", "/api/portal/monthly/defaults")
        assert r["status"] == 401

    def test_post_non_admin_403(self, env):
        seed(env.store, "huayou")
        _, cookie = login(env.port, "huayou", "pw-123456")
        r = request(env.port, "POST", "/api/admin/monthly-defaults",
                    json.dumps({"ids": ["al_a00"]}),
                    {**cookie_header(cookie), "Content-Type": "application/json"})
        assert r["status"] == 403

    def test_post_without_csrf_403(self, env):
        seed(env.store, "admin", role="admin")
        _, cookie = login(env.port, "admin", "pw-123456")
        r = request(env.port, "POST", "/api/admin/monthly-defaults",
                    json.dumps({"ids": ["al_a00"]}),
                    {**cookie_header(cookie), "Content-Type": "application/json"})
        assert r["status"] == 403

    def test_post_invalid_id_400(self, env):
        self._seed_config(env)
        seed(env.store, "admin", role="admin")
        _, cookie = login(env.port, "admin", "pw-123456")
        csrf = json.loads(request(env.port, "GET", "/api/auth/csrf",
                                  headers=cookie_header(cookie))["body"])["csrf_token"]
        r = request(env.port, "POST", "/api/admin/monthly-defaults",
                    json.dumps({"ids": ["not_a_real_id"]}),
                    {**cookie_header(cookie), "Content-Type": "application/json",
                     "X-CSRF": csrf})
        assert r["status"] == 400
        assert "无效产品ID" in r["body"]

    def test_post_persists_and_get_roundtrip(self, env):
        self._seed_config(env)
        seed(env.store, "admin", role="admin")
        _, cookie = login(env.port, "admin", "pw-123456")
        csrf = json.loads(request(env.port, "GET", "/api/auth/csrf",
                                  headers=cookie_header(cookie))["body"])["csrf_token"]
        r = request(env.port, "POST", "/api/admin/monthly-defaults",
                    json.dumps({"ids": ["al_a00", "cu_1e"]}),
                    {**cookie_header(cookie), "Content-Type": "application/json",
                     "X-CSRF": csrf})
        assert r["status"] == 200 and json.loads(r["body"])["ok"] is True
        g = request(env.port, "GET", "/api/portal/monthly/defaults",
                    headers=cookie_header(cookie))
        assert g["status"] == 200
        assert json.loads(g["body"])["ids"] == ["al_a00", "cu_1e"]

    def test_post_empty_resets_to_full(self, env):
        seed(env.store, "admin", role="admin")
        _, cookie = login(env.port, "admin", "pw-123456")
        csrf = json.loads(request(env.port, "GET", "/api/auth/csrf",
                                  headers=cookie_header(cookie))["body"])["csrf_token"]
        r = request(env.port, "POST", "/api/admin/monthly-defaults",
                    json.dumps({"ids": []}),
                    {**cookie_header(cookie), "Content-Type": "application/json",
                     "X-CSRF": csrf})
        assert r["status"] == 200
        g = request(env.port, "GET", "/api/portal/monthly/defaults",
                    headers=cookie_header(cookie))
        assert json.loads(g["body"])["ids"] == []

# ── 邀请码（需求2） ──────────────────────────────────

INVITE_PORTAL_YAML = """portal:
  title: "锂电价格与回收业务数据中心"
  subtitle: "test"
auth:
  session_ttl_hours: 10
  admin_session_ttl_hours: 4
  cookie_secure: false
  lockout_max_failures: 5
  lockout_window_minutes: 10
  lockout_minutes: 10
  password_min_length: 8
  invite_required: true
health:
  cache_seconds: 30
tasks: []
"""


@pytest.fixture
def invite_env(env):
    """开关开启环境：重写 portal.yaml 为 invite_required:true 并重置配置缓存。"""
    env.root.joinpath("portal.yaml").write_text(INVITE_PORTAL_YAML, encoding="utf-8")
    env.mod._portal_cfg_cache["mtime"] = 0.0
    env.mod._portal_cfg_cache["data"] = {}
    return env


def login_invite(port, username, password, invite_code=None):
    payload = {"username": username, "password": password}
    if invite_code is not None:
        payload["invite_code"] = invite_code
    r = request(port, "POST", "/api/auth/login", json.dumps(payload),
                {"Content-Type": "application/json"})
    cookie = None
    for k, v in r["headers"].items():
        if k == "set-cookie":
            cookie = v.split(";")[0]
    return r, cookie


def bind_code(store, username, code):
    """给用户绑定邀请码（复用 rebind 通道）。"""
    u = store.get_user(username)
    assert u, "user missing"
    assert store.rebind_invite(u["id"], code, "admin"), "bind failed"


class TestInviteGateEnabled:
    def test_login_without_invite_rejected(self, invite_env):
        seed(invite_env.store, "huayou")
        bind_code(invite_env.store, "huayou", "H-BIND-0001")
        r, cookie = login_invite(invite_env.port, "huayou", "pw-123456")
        assert r["status"] == 401
        assert "邀请码" in r["body"]
        assert cookie is None

    def test_login_wrong_invite_after_valid_password_401(self, invite_env):
        seed(invite_env.store, "huayou")
        bind_code(invite_env.store, "huayou", "H-BIND-0001")
        r, _ = login_invite(invite_env.port, "huayou", "pw-123456", "WRONG-CODE")
        assert r["status"] == 401
        assert "邀请码" in r["body"]

    def test_login_bound_invite_ok(self, invite_env):
        seed(invite_env.store, "huayou")
        bind_code(invite_env.store, "huayou", "H-BIND-0001")
        r, cookie = login_invite(invite_env.port, "huayou", "pw-123456", "H-BIND-0001")
        assert r["status"] == 200 and json.loads(r["body"])["ok"] is True
        assert cookie

    def test_register_invalid_code_400(self, invite_env):
        r = request(invite_env.port, "POST", "/api/auth/register",
                    json.dumps({"username": "rookie01", "password": "pw-12345678",
                                "invite_code": "BAD"}),
                    {"Content-Type": "application/json"})
        assert r["status"] == 400
        assert "邀请码" in r["body"]

    def test_register_consume_then_login_same_code(self, invite_env):
        invite_env.store.create_invite("H-SHARE-001", "admin", max_uses=2)
        r = request(invite_env.port, "POST", "/api/auth/register",
                    json.dumps({"username": "rookie02", "password": "pw-12345678",
                                "invite_code": "H-SHARE-001"}),
                    {"Content-Type": "application/json"})
        assert r["status"] == 201
        r2, cookie = login_invite(invite_env.port, "rookie02", "pw-12345678", "H-SHARE-001")
        assert r2["status"] == 200 and cookie

    def test_revoked_binding_blocks_login(self, invite_env):
        """注册消费父码（子行绑定）→ 撤销父码 → 该用户登录被拒（恢复提示文案）。"""
        info = invite_env.store.create_invite("H-REVK-0001", "admin")
        st, _, uid = invite_env.store.register_with_invite(
            "revoked01", "pw-12345678", "H-REVK-0001")
        assert st == "ok" and uid
        r1, _ = login_invite(invite_env.port, "revoked01", "pw-12345678", "H-REVK-0001")
        assert r1["status"] == 200
        invite_env.store.revoke_invite(info["id"], "admin")
        r2, _ = login_invite(invite_env.port, "revoked01", "pw-12345678", "H-REVK-0001")
        assert r2["status"] == 401
        assert "重新绑定" in r2["body"]

    def test_rebind_new_code_logs_in(self, invite_env):
        seed(invite_env.store, "huayou")
        bind_code(invite_env.store, "huayou", "H-OLD-00001")
        assert login_invite(invite_env.port, "huayou", "pw-123456", "H-OLD-00001")[0]["status"] == 200
        bind_code(invite_env.store, "huayou", "H-NEW-00001")
        assert login_invite(invite_env.port, "huayou", "pw-123456", "H-OLD-00001")[0]["status"] == 401
        assert login_invite(invite_env.port, "huayou", "pw-123456", "H-NEW-00001")[0]["status"] == 200

    def test_switch_off_legacy_login_works(self, env):
        """开关关闭（默认 env）：登录不带码照常成功——旧流程回归。"""
        seed(env.store, "huayou")
        r, cookie = login(env.port, "huayou", "pw-123456")
        assert r["status"] == 200 and cookie


class TestInviteAdminApi:
    def test_non_admin_403(self, env):
        seed(env.store, "huayou")
        _, cookie = login(env.port, "huayou", "pw-123456")
        assert request(env.port, "GET", "/api/admin/invites",
                       headers=cookie_header(cookie))["status"] == 403
        assert request(env.port, "POST", "/api/admin/invites",
                       json.dumps({"action": "create"}),
                       {**cookie_header(cookie), "Content-Type": "application/json"})["status"] == 403

    def test_post_without_csrf_403(self, env):
        seed(env.store, "admin", role="admin")
        _, cookie = login(env.port, "admin", "pw-123456")
        r = request(env.port, "POST", "/api/admin/invites",
                    json.dumps({"action": "create"}),
                    {**cookie_header(cookie), "Content-Type": "application/json"})
        assert r["status"] == 403

    def test_admin_create_returns_code_once_and_list_shows_prefix(self, env):
        seed(env.store, "admin", role="admin")
        _, cookie = login(env.port, "admin", "pw-123456")
        csrf = json.loads(request(env.port, "GET", "/api/auth/csrf",
                                  headers=cookie_header(cookie))["body"])["csrf_token"]
        r = request(env.port, "POST", "/api/admin/invites",
                    json.dumps({"action": "create", "max_uses": 2, "note": "t"}),
                    {**cookie_header(cookie), "Content-Type": "application/json",
                     "X-CSRF": csrf})
        assert r["status"] == 201
        j = json.loads(r["body"])
        assert j["ok"] is True and j["code"]
        assert j["invite"]["code_prefix"] == j["code"][:4]
        g = request(env.port, "GET", "/api/admin/invites", headers=cookie_header(cookie))
        assert g["status"] == 200
        invites = json.loads(g["body"])["invites"]
        assert any(i["code_prefix"] == j["code"][:4] for i in invites)
        # 列表绝不泄露明文
        assert j["code"] not in g["body"]
        assert "code_hash" not in g["body"]

    def test_admin_revoke_and_rebind(self, env):
        seed(env.store, "admin", role="admin")
        seed(env.store, "huayou")
        _, cookie = login(env.port, "admin", "pw-123456")
        csrf = json.loads(request(env.port, "GET", "/api/auth/csrf",
                                  headers=cookie_header(cookie))["body"])["csrf_token"]
        head = {**cookie_header(cookie), "Content-Type": "application/json", "X-CSRF": csrf}
        r1 = request(env.port, "POST", "/api/admin/invites",
                     json.dumps({"action": "create"}), head)
        iid = json.loads(r1["body"])["invite"]["id"]
        r2 = request(env.port, "POST", "/api/admin/invites",
                     json.dumps({"action": "revoke", "id": iid}), head)
        assert r2["status"] == 200
        u = env.store.get_user("huayou")
        r3 = request(env.port, "POST", "/api/admin/invites",
                     json.dumps({"action": "rebind", "user_id": u["id"]}), head)
        assert r3["status"] == 201
        j3 = json.loads(r3["body"])
        assert j3["code"] and j3["invite"]["bound_user_id"] == u["id"]
        # rebind 未知用户
        r4 = request(env.port, "POST", "/api/admin/invites",
                     json.dumps({"action": "rebind", "user_id": 9999}), head)
        assert r4["status"] == 400
