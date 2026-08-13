/* 账号安全：查看身份 + 修改密码（user / admin 共用） */
"use strict";

(function () {
  API.me().then((me) => {
    if (!me) return;
    document.getElementById("acc-username").textContent = me.username;
    document.getElementById("acc-role").textContent = me.role === "admin" ? "管理员" : "普通用户";
    if (me.must_change_password) {
      document.getElementById("must-change-banner").hidden = false;
    }
  });

  const form = document.getElementById("pwd-form");
  const errEl = document.getElementById("pwd-error");

  function showError(msg) {
    errEl.textContent = msg;
    errEl.hidden = false;
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errEl.hidden = true;
    const current = document.getElementById("pwd-current").value;
    const p1 = document.getElementById("pwd-new").value;
    const p2 = document.getElementById("pwd-confirm").value;
    if (p1 !== p2) { showError("两次输入的新密码不一致"); return; }
    if (p1.length < 8) { showError("新密码长度至少 8 位"); return; }
    try {
      const data = await API.post("/api/auth/change-password", { current, new: p1 });
      toast(data.message || "密码已修改，请重新登录", "ok");
      setTimeout(() => location.replace("/login?changed=1"), 1200);
    } catch (err) {
      showError((err.data && err.data.error) || "修改失败，请稍后重试");
    }
  });
})();
