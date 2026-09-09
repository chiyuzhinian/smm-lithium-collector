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
        <div class="brand-name">华友锂电行情数据中心</div>
        <div class="brand-sub">HUAYOU LITHIUM MARKET DATA</div>
      </span>
    </a>
    <nav class="nav" id="main-nav">${links}</nav>
    <div class="user-area" id="user-area"></div>
  </div>
</header>`;
    renderUserArea(page);
  }
  const footEl = document.getElementById("footer");
  if (footEl) {
    footEl.outerHTML = `
<div class="footer">
  <div><b>华友锂电行情数据中心</b> · 数据来源：SMM 上海有色网公开报价（采集器自动采集，采集时间与报价日期分别标注）</div>
  <div>日均价为对应报价日期的 SMM 源字段；月均价为依据采集报价计算的期间算术平均，非 SMM 官方月均 · 涨红跌绿</div>
</div>`;
  }
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
