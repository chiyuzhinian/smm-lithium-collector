/* 登录页 */
"use strict";

(function () {
  const form = document.getElementById("login-form");
  const errEl = document.getElementById("login-error");
  const btn = document.getElementById("login-btn");
  let countdownTimer = null;

  function nextParam() {
    const m = location.search.match(/[?&]next=([^&]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  }

  // 已登录则直接跳回
  API.me().then((me) => {
    if (me) location.replace(nextParam() || "/");
  });

  function showError(msg) {
    errEl.textContent = msg;
    errEl.hidden = false;
  }

  function startCountdown(seconds) {
    if (countdownTimer) clearInterval(countdownTimer);
    const until = Date.now() + seconds * 1000;
    const tick = () => {
      const left = Math.max(0, Math.ceil((until - Date.now()) / 1000));
      if (left <= 0) {
        clearInterval(countdownTimer);
        btn.disabled = false;
        showError("用户名或密码错误");
        return;
      }
      btn.disabled = true;
      showError("尝试次数过多，请 " + left + " 秒后再试");
    };
    tick();
    countdownTimer = setInterval(tick, 1000);
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errEl.hidden = true;
    btn.disabled = true;
    const username = document.getElementById("username").value.trim();
    const password = document.getElementById("password").value;
    try {
      const data = await API.post("/api/auth/login",
        { username, password, next: nextParam() }, { csrf: false });
      // 初始密码 → 强制去改密页；否则回跳原页面
      location.replace(data.must_change_password ? "/account" : (data.next || "/"));
    } catch (err) {
      if (err.status === 429 && err.data && err.data.retry_after) {
        startCountdown(err.data.retry_after);
      } else {
        showError((err.data && err.data.error) || "用户名或密码错误");
        btn.disabled = false;
      }
    }
  });
})();
