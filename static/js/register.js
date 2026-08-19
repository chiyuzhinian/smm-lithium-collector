/* 注册页（仅用户名 + 密码两字段；角色由服务端强制为普通用户） */
"use strict";

(function () {
  const form = document.getElementById("register-form");
  const errEl = document.getElementById("register-error");
  const btn = document.getElementById("register-btn");

  function showError(msg) {
    errEl.textContent = msg;
    errEl.hidden = false;
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errEl.hidden = true;
    btn.disabled = true;
    const username = document.getElementById("username").value.trim();
    const password = document.getElementById("password").value;
    try {
      // 注册端点公开（注册前无会话），CSRF 豁免
      await API.post("/api/auth/register", { username, password }, { csrf: false });
      location.replace("/login?registered=1");
    } catch (err) {
      showError((err.data && err.data.error) || "注册失败，请稍后再试");
      btn.disabled = false;
    }
  });
})();
