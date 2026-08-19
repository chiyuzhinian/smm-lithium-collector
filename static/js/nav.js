/* 共享导航栏与页脚渲染（含按角色的账号区） */
"use strict";

const NAV_LINKS = [
  ["/", "首页"],
  ["/today", "今日价格"],
  ["/history", "历史数据"],
  ["/topics", "业务专题"],
];

document.addEventListener("DOMContentLoaded", () => {
  const page = document.body.dataset.page || "";
  const navEl = document.getElementById("navbar");
  if (navEl) {
    const links = NAV_LINKS.map(([href, label]) => {
      const active = page === (href === "/" ? "home" : href.slice(1)) ? ' class="active" aria-current="page"' : "";
      return `<li><a href="${href}"${active}>${label}</a></li>`;
    }).join("");
    navEl.outerHTML = `
<nav class="navbar">
  <div class="navbar-inner">
    <div class="navbar-brand"><span class="icon">⚡</span> 锂电价格与回收业务数据中心</div>
    <div class="navbar-right">
      <ul class="navbar-links">${links}</ul>
      <ul class="navbar-links" id="navbar-auth"></ul>
    </div>
  </div>
</nav>`;
    renderAuthLinks(page);
  }
  const footEl = document.getElementById("footer");
  if (footEl) {
    footEl.outerHTML = `
<div class="footer">
  <div class="footer-title">锂电价格与回收业务数据中心</div>
  <div class="footer-line">聚焦锂电价格、材料价格、基础金属与回收链价格数据 · 数据用于内部分析与行业研究</div>
  <div class="footer-line">数据来源：SMM 上海有色网公开报价（采集器自动采集）</div>
</div>`;
  }
});

/* 账号区：未登录显示「登录」；admin 额外显示「运维中心」 */
function renderAuthLinks(page) {
  const el = document.getElementById("navbar-auth");
  if (!el) return;
  API.me().then((me) => {
    if (!me) {
      el.innerHTML = `<li><a href="/login">登录</a></li>`;
      return;
    }
    const isAdmin = me.role === "admin";
    const parts = [
      // 数据质量与运维中心仅管理员可见（服务端已做同等校验，这里只是隐藏入口）
      isAdmin ? `<li><a href="/quality"${page === "quality" ? ' class="active"' : ""}>数据质量</a></li>` : "",
      isAdmin ? `<li><a href="/admin"${page === "admin" ? ' class="active"' : ""}>运维中心</a></li>` : "",
      `<li><a href="/account"${page === "account" ? ' class="active"' : ""}>账号</a></li>`,
      `<li><a href="#" id="logout-link">退出登录</a></li>`,
    ];
    el.innerHTML = parts.join("");
    const logout = document.getElementById("logout-link");
    if (logout) {
      logout.addEventListener("click", async (e) => {
        e.preventDefault();
        try { await API.post("/api/auth/logout"); } catch (err) { /* 忽略 */ }
        location.href = "/login";
      });
    }
  });
}
