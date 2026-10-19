/* 共享导航（新版）：紧凑品牌顶栏 + 主导航 + 按角色的用户区 */
"use strict";

const NAV_LINKS = [
  ["/", "quotes", "每日报价"],
  ["/trends", "trends", "价格走势"],
  ["/reports", "reports", "数据与报表"],
];

document.addEventListener("DOMContentLoaded", () => {
  const page = document.body.dataset.page || "";
  const navEl = document.getElementById("navbar");
  if (navEl) {
    const links = NAV_LINKS.map(([href, key, label]) => {
      const active = page === key ? " active" : "";
      return `<a class="nav-link${active}" href="${href}"${active ? ' aria-current="page"' : ""}>${label}</a>`;
    }).join("");
    navEl.outerHTML = `
<header class="topbar">
  <div class="topbar-inner">
    <a class="brand" href="/" style="text-decoration:none">
      <span class="brand-mark">锂</span>
      <span>
        <div class="brand-name">锂电价格与回收业务数据中心</div>
        <div class="brand-sub">LITHIUM PRICE &amp; RECYCLING DATA CENTER</div>
      </span>
    </a>
    <nav class="nav" id="main-nav">${links}</nav>
    <div class="user-area" id="user-area"></div>
  </div>
</header>`;
    renderUserArea(page);
  }
  /* R2：全站页脚说明已按业务要求移除（原 footer 注入块删除）；
     各页面的 <div id="footer"> 占位保留为空，不渲染任何内容。 */
});

/* 用户区：未登录显示「登录」；admin 额外显示数据质量/运维中心 */
function renderUserArea(page) {
  const el = document.getElementById("user-area");
  if (!el) return;
  API.me().then((me) => {
    if (!me) {
      el.innerHTML = `<a class="btn sm" href="/login">登录</a>`;
      return;
    }
    const isAdmin = me.role === "admin";
    const initial = (me.username || "?").slice(0, 1).toUpperCase();
    const adminLinks = isAdmin
      ? `<a class="nav-link admin-link${page === "quality" ? " active" : ""}" href="/quality">数据质量</a>
         <a class="nav-link admin-link${page === "admin" ? " active" : ""}" href="/admin">运维中心</a>`
      : "";
    const nav = document.getElementById("main-nav");
    if (nav) nav.insertAdjacentHTML("beforeend", adminLinks);
    el.innerHTML = `
      <span class="user-chip">
        <span class="user-avatar">${escHtml(initial)}</span>
        <span class="user-name">${escHtml(me.username)}</span>
        <span class="role-tag ${isAdmin ? "admin" : "user"}">${isAdmin ? "管理员" : "用户"}</span>
      </span>
      <a class="text-link" href="/account">账号</a>
      <button class="text-link danger" id="logout-link">退出</button>`;
    document.getElementById("logout-link").addEventListener("click", async (e) => {
      e.preventDefault();
      try { await API.post("/api/auth/logout"); } catch (err) { /* 忽略 */ }
      location.href = "/login";
    });
  });
}
