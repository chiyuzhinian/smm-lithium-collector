"""Phase C — 保守型自动账号密码登录（`guarded_auto_login`）单元测试。

策略：用 FakePage/FakeLocator 完全模拟 Playwright Page，不依赖真实浏览器或网络。

覆盖以下场景：
  1. 正确登录流程 → 跳转目标页 → AUTH_OK
  2. 错误密码 → 提交后页面含「密码错误」→ AUTH_LOGIN_FAILED
  3. 登录页即出现验证码 / 短信验证 / 滑块 / 风险检测 → AUTH_VERIFICATION_REQUIRED
  4. 提交后页面出现验证挑战 → AUTH_VERIFICATION_REQUIRED
  5. page.goto 抛网络错误 → AUTH_NETWORK_ERROR
  6. 单次自动登录额度用尽 → 第二次直接 EXPIRED，不再尝试
  7. 凭据 / login_url 未配置 → EXPIRED
  8. config.selectors.login.username 自定义选择器生效
  9. 内部 _classify_text / _selectors_from_config 辅助函数
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from smm_collector import authentication as auth


# ── Fakes ──────────────────────────────────────────────────


class _FakeFillable:
    """支持 fill / click / wait_for 的最小化 Locator。"""

    def __init__(self, *, visible: bool = True,
                 raise_on_fill: Exception | None = None,
                 raise_on_click: Exception | None = None):
        self._visible = visible
        self._raise_on_fill = raise_on_fill
        self._raise_on_click = raise_on_click
        self.fill_calls: list[str] = []
        self.click_calls: int = 0

    async def fill(self, value: str) -> None:
        self.fill_calls.append(value)
        if self._raise_on_fill:
            raise self._raise_on_fill

    async def click(self) -> None:
        self.click_calls += 1
        if self._raise_on_click:
            raise self._raise_on_click

    async def wait_for(self, state: str = "visible", timeout: int = 0) -> None:
        if state == "visible" and not self._visible:
            from playwright.async_api import TimeoutError as PWTimeout
            raise PWTimeout(f"locator not visible (timeout={timeout}ms)")


class _FakeLocator:
    """支持 .first / .wait_for(state=visible) / .inner_text 的 locator。

    默认 ``_visible=False``（必须显式打开），避免「忘记配置 → 默认可见 → 误命中」。
    """

    def __init__(self, *, visible: bool = False, inner_text: str = ""):
        self._visible = visible
        self._inner_text = inner_text

    @property
    def first(self) -> "_FakeLocator":
        return self

    async def wait_for(self, state: str = "visible", timeout: int = 0) -> None:
        if state == "visible" and not self._visible:
            from playwright.async_api import TimeoutError as PWTimeout
            raise PWTimeout(f"locator not visible (timeout={timeout}ms)")

    async def inner_text(self) -> str:
        return self._inner_text

    async def count(self) -> int:
        return 1 if self._visible else 0

    async def fill(self, value: str) -> None:
        return None

    async def click(self) -> None:
        return None


class _FakePage:
    """支持 goto / locator / wait_for_timeout / url / content 的最小化 Page。

    各 hook 由测试设置：

    - ``goto_results``: list of exceptions 或 None 描述逐次 goto 结果
    - ``current_url``: 当前页面 URL
    - ``current_html``: 当前页面 HTML 片段
    - ``current_body_text``: 当前 body inner_text
    - ``locator_behavior``: dict[selector] -> {visible, inner_text, raise_on_fill, raise_on_click}

    默认所有 selector **不可见**，必须显式设置 visible=True。
    """

    def __init__(self,
                 current_url: str = "https://user.smm.cn/login",
                 current_body_text: str = "请输入账号和密码",
                 current_html: str = "<html></html>",
                 goto_results: list[Any] | None = None,
                 locator_behavior: dict[str, dict] | None = None,
                 goto_raise: Exception | None = None):
        self._current_url = current_url
        self._current_body = current_body_text
        self._current_html = current_html
        self._goto_results = list(goto_results or [])
        self._goto_raise = goto_raise
        self._locator_behavior = locator_behavior or {}
        self.wait_for_timeout_log: list[int] = []
        self.goto_log: list[tuple[str, dict]] = []
        self.locator_calls: list[str] = []
        self._attr: dict[str, Any] = {}

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)

    def __getattr__(self, name):
        # 仅在 _attr 中显式设置过的属性才返回
        if name in self._attr:
            return self._attr[name]
        raise AttributeError(name)

    @property
    def url(self) -> str:
        return self._current_url

    def set_url(self, url: str) -> None:
        self._current_url = url

    def set_body(self, body: str) -> None:
        self._current_body = body

    def set_html(self, html: str) -> None:
        self._current_html = html

    async def wait_for_timeout(self, ms: int) -> None:
        self.wait_for_timeout_log.append(ms)
        return None

    async def goto(self, url: str, **kwargs) -> None:
        self.goto_log.append((url, kwargs))
        if self._goto_raise is not None:
            raise self._goto_raise
        if self._goto_results:
            result = self._goto_results.pop(0)
            if isinstance(result, Exception):
                raise result
        # 默认：成功 goto，URL 保持
        return None

    async def content(self) -> str:
        return self._current_html

    def locator(self, selector: str) -> _FakeLocator:
        self.locator_calls.append(selector)
        if selector == "body":
            return _FakeLocator(visible=True, inner_text=self._current_body)
        behavior = self._locator_behavior.get(selector) or {}
        return _FakeLocator(
            visible=bool(behavior.get("visible", False)),
            inner_text=str(behavior.get("inner_text", "")),
        )


# ── Helpers ────────────────────────────────────────────────


def _basic_cfg(**overrides) -> SimpleNamespace:
    cfg = SimpleNamespace(
        username="alice",
        password="hunter2-XYZ",
        login_url="https://user.smm.cn/login",
        target_url="https://new-energy.smm.cn/new_energy/14042",
        selectors={},
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _login_page_locator_behavior() -> dict[str, dict]:
    """默认登录页 locator 配置：username/password/submit 均可见。"""
    return {
        'input[name="username"]': {"visible": True},
        'input[type="password"]': {"visible": True},
        'button[type="submit"]': {"visible": True},
    }


# ── 1) 成功登录 ────────────────────────────────────────────


def test_guarded_auto_login_success():
    """login page 提交后页面跳转走 → AUTH_OK。"""
    # 流程：goto login → body 无验证信号 → 填表 → 提交 → 等待中 URL 变化
    # 等待 1s 后的 URL 通过 set_url 切换为目标页
    page = _FakePage(
        current_url="https://user.smm.cn/login",
        current_body_text="欢迎登录",  # 含 "登录" 但不含验证挑战
        goto_results=[
            None,  # 1. 第一次 goto(login_url)
            None,  # 2. 第二次 goto(target_url) for re-verify
        ],
    )
    page.set_url = page.set_url  # type: ignore[attr-defined]

    # 模拟等待中 URL 切换
    original_wait = page.wait_for_timeout

    async def wait_then_redirect(ms: int):
        await original_wait(ms)
        if page._current_url.startswith("https://user.smm.cn/login"):
            # 第一次 submit 后第 2s 时跳走
            if len([x for x in page.wait_for_timeout_log if x == 1000]) >= 2:
                page.set_url("https://new-energy.smm.cn/new_energy/14042")
                page.set_body("锂电现货价格")
                page.set_html("<html>price</html>")

    page.wait_for_timeout = wait_then_redirect  # type: ignore[assignment]
    page._locator_behavior = _login_page_locator_behavior()
    cfg = _basic_cfg()
    status, reason = asyncio.run(auth.guarded_auto_login(page, cfg))
    assert status == "AUTH_OK"
    assert "form submitted" in reason


# ── 2) 错误密码 → LOGIN_FAILED ──────────────────────────────


def test_guarded_auto_login_wrong_password():
    """提交后页面含「密码错误」→ AUTH_LOGIN_FAILED。"""
    page = _FakePage(
        current_url="https://user.smm.cn/login",
        current_body_text="欢迎登录",
    )
    page._locator_behavior = _login_page_locator_behavior()

    # 让 wait_for_timeout 在 2s 时切换 body 到错误提示
    original_wait = page.wait_for_timeout

    async def wait_then_show_error(ms: int):
        await original_wait(ms)
        if len([x for x in page.wait_for_timeout_log if x == 1000]) == 2:
            page.set_body("账号或密码错误，请重新输入")

    page.wait_for_timeout = wait_then_show_error  # type: ignore[assignment]
    cfg = _basic_cfg()
    status, reason = asyncio.run(auth.guarded_auto_login(page, cfg))
    assert status == "AUTH_LOGIN_FAILED"


# ── 3) 登录页即出现验证挑战 → VERIFICATION_REQUIRED ───────────


@pytest.mark.parametrize("signal", ["验证码", "短信验证", "滑块验证", "安全验证", "风险检测", "图形验证"])
def test_guarded_auto_login_verification_on_load(signal):
    """登录页 body 立即含验证关键字 → 立即返回 VERIFICATION_REQUIRED（不尝试填表）。"""
    page = _FakePage(
        current_url="https://user.smm.cn/login",
        current_body_text=f"请输入{signal}",
    )
    page._locator_behavior = _login_page_locator_behavior()
    cfg = _basic_cfg()
    status, reason = asyncio.run(auth.guarded_auto_login(page, cfg))
    assert status == "AUTH_VERIFICATION_REQUIRED"
    # 关键：未触发 fill（因为 _fill_login_form 之前就退出）
    assert "未尝试填表" in reason or "登录页" in reason


# ── 4) 提交后才出现验证挑战 → VERIFICATION_REQUIRED ─────────


def test_guarded_auto_login_verification_after_submit():
    """填表后 5s 时页面出现验证码 → 返回 VERIFICATION_REQUIRED。"""
    page = _FakePage(
        current_url="https://user.smm.cn/login",
        current_body_text="欢迎登录",
    )
    page._locator_behavior = _login_page_locator_behavior()
    original_wait = page.wait_for_timeout

    async def wait_then_show_captcha(ms: int):
        await original_wait(ms)
        if len([x for x in page.wait_for_timeout_log if x == 1000]) == 3:
            page.set_body("请输入验证码")

    page.wait_for_timeout = wait_then_show_captcha  # type: ignore[assignment]
    cfg = _basic_cfg()
    status, reason = asyncio.run(auth.guarded_auto_login(page, cfg))
    assert status == "AUTH_VERIFICATION_REQUIRED"


# ── 5) page.goto 网络错误 → NETWORK_ERROR ──────────────────


def test_guarded_auto_login_goto_network_error():
    """goto 抛 net::ERR → AUTH_NETWORK_ERROR。"""
    page = _FakePage(
        current_url="https://user.smm.cn/login",
        current_body_text="",
        goto_raise=Exception("net::ERR_PROXY_CONNECTION_FAILED at login page"),
    )
    page._locator_behavior = _login_page_locator_behavior()
    cfg = _basic_cfg()
    status, reason = asyncio.run(auth.guarded_auto_login(page, cfg))
    assert status == "AUTH_NETWORK_ERROR"
    assert "网络" in reason or "net::" in reason.lower() or "PROXY" in reason


def test_guarded_auto_login_goto_timeout():
    """goto 抛 PlaywrightTimeoutError → AUTH_NETWORK_ERROR。"""
    from playwright.async_api import TimeoutError as PWTimeout
    page = _FakePage(
        current_url="https://user.smm.cn/login",
        current_body_text="",
        goto_raise=PWTimeout("Timeout 30000ms exceeded"),
    )
    page._locator_behavior = _login_page_locator_behavior()
    cfg = _basic_cfg()
    status, reason = asyncio.run(auth.guarded_auto_login(page, cfg))
    assert status == "AUTH_NETWORK_ERROR"
    assert "超时" in reason or "Timeout" in reason


# ── 6) 单次自动登录额度用尽 → 第二次不尝试 ────────────────


def test_guarded_auto_login_single_attempt_limit():
    """page 已用过额度 → 第二次直接 EXPIRED，不再尝试。"""
    page = _FakePage(
        current_url="https://user.smm.cn/login",
        current_body_text="欢迎登录",
    )
    page._locator_behavior = _login_page_locator_behavior()
    setattr(page, auth._AUTO_LOGIN_ATTEMPTS_KEY, 1)
    cfg = _basic_cfg()
    status, reason = asyncio.run(auth.guarded_auto_login(page, cfg, max_attempts=1))
    assert status == "AUTH_EXPIRED"
    assert "used up" in reason
    # 没有触发 goto（说明确实没尝试）
    assert all("user.smm.cn" not in url for url, _ in page.goto_log)


# ── 7) 凭据 / login_url 未配置 ─────────────────────────────


def test_guarded_auto_login_no_credentials():
    """username 或 password 为空 → AUTH_EXPIRED。"""
    page = _FakePage(
        current_url="https://user.smm.cn/login",
        current_body_text="欢迎登录",
    )
    page._locator_behavior = _login_page_locator_behavior()
    cfg = _basic_cfg(username="", password="hunter2")
    status, reason = asyncio.run(auth.guarded_auto_login(page, cfg))
    assert status == "AUTH_EXPIRED"
    assert "用户名" in reason or "未配置" in reason


def test_guarded_auto_login_no_login_url():
    """login_url 为空 → AUTH_EXPIRED。"""
    page = _FakePage(
        current_url="https://user.smm.cn/login",
        current_body_text="",
    )
    page._locator_behavior = _login_page_locator_behavior()
    cfg = _basic_cfg(login_url="")
    status, reason = asyncio.run(auth.guarded_auto_login(page, cfg))
    assert status == "AUTH_EXPIRED"


# ── 8) config.selectors.login 自定义选择器 ──────────────────


def test_guarded_auto_login_custom_username_selector():
    """config.selectors.login.username 自定义选择器应优先。"""
    page = _FakePage(
        current_url="https://user.smm.cn/login",
        current_body_text="欢迎登录",
    )
    # 只配置自定义选择器可见
    page._locator_behavior = {
        '#my-username-input': {"visible": True},
        'input[type="password"]': {"visible": True},
        'button[type="submit"]': {"visible": True},
    }
    # 等待 1s 后 URL 跳走（成功）
    original_wait = page.wait_for_timeout

    async def wait_then_redirect(ms: int):
        await original_wait(ms)
        if len([x for x in page.wait_for_timeout_log if x == 1000]) == 2:
            page.set_url("https://new-energy.smm.cn/new_energy/14042")
            page.set_body("价格")
            page.set_html("<html>price</html>")

    page.wait_for_timeout = wait_then_redirect  # type: ignore[assignment]
    cfg = _basic_cfg()
    cfg.selectors = {"login": {"username": "#my-username-input"}}
    status, reason = asyncio.run(auth.guarded_auto_login(page, cfg))
    assert status == "AUTH_OK"
    # 自定义选择器应被命中
    assert "#my-username-input" in page.locator_calls


# ── 9) 内部辅助函数单元测试 ───────────────────────────────


def test_classify_text_verification():
    assert auth._classify_text("请输入验证码") == "verification"
    assert auth._classify_text("短信验证") == "verification"
    assert auth._classify_text("图形验证") == "verification"
    assert auth._classify_text("扫码登录") == "verification"


def test_classify_text_login_failed():
    assert auth._classify_text("账号或密码错误") == "login_failed"
    assert auth._classify_text("Wrong Password") == "login_failed"
    assert auth._classify_text("Account Locked") == "login_failed"


def test_classify_text_unknown():
    assert auth._classify_text("锂电现货价格表") == "unknown"
    assert auth._classify_text("") == "unknown"


def test_selectors_from_config_empty():
    """无 login 段 → 全为 None，由内置兜底接管。"""
    cfg = SimpleNamespace(selectors={})
    sels = auth._selectors_from_config(cfg)
    assert sels == {"username": None, "password": None, "submit": None, "form": None}


def test_selectors_from_config_custom():
    cfg = SimpleNamespace(selectors={
        "login": {"username": "#user", "password": "#pwd", "submit": "#sub"},
    })
    sels = auth._selectors_from_config(cfg)
    assert sels["username"] == "#user"
    assert sels["password"] == "#pwd"
    assert sels["submit"] == "#sub"


def test_selectors_from_config_selectors_none():
    """selectors 为 None → 不抛异常。"""
    cfg = SimpleNamespace(selectors=None)
    sels = auth._selectors_from_config(cfg)
    assert sels["username"] is None


# ── 10) 表单定位失败 → AUTH_EXPIRED ────────────────────────


def test_guarded_auto_login_no_username_input():
    """无任何可见的 username 输入框 → AUTH_EXPIRED（页面结构可能变化）。"""
    page = _FakePage(
        current_url="https://user.smm.cn/login",
        current_body_text="欢迎登录",
    )
    # 不配置任何 selector 可见
    page._locator_behavior = {
        'input[type="password"]': {"visible": True},
        'button[type="submit"]': {"visible": True},
    }
    cfg = _basic_cfg()
    status, reason = asyncio.run(auth.guarded_auto_login(page, cfg))
    assert status == "AUTH_EXPIRED"
    assert "用户名" in reason or "选择器" in reason or "未定位" in reason


def test_guarded_auto_login_no_password_input():
    """无可见 password 输入框 → AUTH_EXPIRED。"""
    page = _FakePage(
        current_url="https://user.smm.cn/login",
        current_body_text="欢迎登录",
    )
    page._locator_behavior = {
        'input[name="username"]': {"visible": True},
        'button[type="submit"]': {"visible": True},
    }
    cfg = _basic_cfg()
    status, reason = asyncio.run(auth.guarded_auto_login(page, cfg))
    assert status == "AUTH_EXPIRED"
    assert "密码" in reason or "未定位" in reason


def test_guarded_auto_login_no_submit_button():
    """无可见 submit 按钮 → AUTH_EXPIRED。"""
    page = _FakePage(
        current_url="https://user.smm.cn/login",
        current_body_text="欢迎登录",
    )
    page._locator_behavior = {
        'input[name="username"]': {"visible": True},
        'input[type="password"]': {"visible": True},
    }
    cfg = _basic_cfg()
    status, reason = asyncio.run(auth.guarded_auto_login(page, cfg))
    assert status == "AUTH_EXPIRED"
    assert "提交" in reason or "未定位" in reason


# ── 11) 提交后超过 deadline 仍未离开登录页 → AUTH_EXPIRED ─


def test_guarded_auto_login_no_redirect_within_deadline():
    """等待 15s URL 仍未变 → AUTH_EXPIRED。"""
    page = _FakePage(
        current_url="https://user.smm.cn/login",
        current_body_text="欢迎登录",
    )
    page._locator_behavior = _login_page_locator_behavior()
    # 不重写 wait_for_timeout，URL 永远不变
    cfg = _basic_cfg()
    status, reason = asyncio.run(auth.guarded_auto_login(page, cfg))
    assert status == "AUTH_EXPIRED"
    assert "未离开登录页" in reason or "deadline" in reason.lower() or "15s" in reason


# ── 12) 提交后目标页复验失败（net::ERR） → NETWORK_ERROR ──


def test_guarded_auto_login_target_reverify_network_error():
    """成功离开 login 后，target_url 复验抛 net::ERR → NETWORK_ERROR。"""
    page = _FakePage(
        current_url="https://user.smm.cn/login",
        current_body_text="欢迎登录",
    )
    page._locator_behavior = _login_page_locator_behavior()
    original_wait = page.wait_for_timeout

    async def wait_then_redirect(ms: int):
        await original_wait(ms)
        if len([x for x in page.wait_for_timeout_log if x == 1000]) == 2:
            page.set_url("https://new-energy.smm.cn/new_energy/14042")
            page.set_body("价格")
            page.set_html("<html>price</html>")

    page.wait_for_timeout = wait_then_redirect  # type: ignore[assignment]
    # 第二次 goto 抛网络错误
    page._goto_results = [None, Exception("net::ERR_CONNECTION_RESET")]
    cfg = _basic_cfg()
    status, reason = asyncio.run(auth.guarded_auto_login(page, cfg))
    assert status == "AUTH_NETWORK_ERROR"
    assert "复验" in reason or "net::" in reason.lower() or "RESET" in reason


# ── 13) 凭据 / URL 不可在日志中出现（静态断言） ────────────


def test_no_credential_in_error_messages():
    """任何返回的 reason 不得含 username/password 字面值。"""
    page = _FakePage(
        current_url="https://user.smm.cn/login",
        current_body_text="欢迎登录",
    )
    page._locator_behavior = _login_page_locator_behavior()
    cfg = _basic_cfg()
    status, reason = asyncio.run(auth.guarded_auto_login(page, cfg))
    # 不含 username 完整值
    assert "alice" not in reason
    # 不含 password 完整值
    assert "hunter2-XYZ" not in reason
    # 包含的只是元信息
    assert status in (
        "AUTH_OK", "AUTH_EXPIRED", "AUTH_VERIFICATION_REQUIRED",
        "AUTH_LOGIN_FAILED", "AUTH_NETWORK_ERROR", "AUTH_UNKNOWN",
    )
