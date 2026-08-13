/* 共享 API 封装与格式化工具（所有页面依赖，需最先引入） */
"use strict";

const API = {
  cache: {},
  async get(path, ttlMs = 30000) {
    const now = Date.now();
    const hit = this.cache[path];
    if (hit && now - hit.ts < ttlMs) return hit.data;
    const resp = await fetch(path);
    this._handleAuth(resp, path);
    if (!resp.ok) throw new Error(path + " → HTTP " + resp.status);
    const data = await resp.json();
    this.cache[path] = { ts: now, data };
    return data;
  },

  async post(path, body, opts = {}) {
    const headers = { "Content-Type": "application/json" };
    if (opts.csrf !== false) {
      const csrf = await this.csrfToken();
      if (csrf) headers["X-CSRF"] = csrf;
    }
    const resp = await fetch(path, {
      method: "POST",
      headers,
      body: JSON.stringify(body || {}),
    });
    this._handleAuth(resp, path);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const err = new Error(data.error || (path + " → HTTP " + resp.status));
      err.status = resp.status;
      err.data = data;
      throw err;
    }
    return data;
  },

  async csrfToken() {
    if (this._csrf) return this._csrf;
    try {
      const resp = await fetch("/api/auth/csrf");
      if (resp.ok) {
        const data = await resp.json();
        this._csrf = data.csrf_token || "";
      }
    } catch (e) { /* 忽略：POST 会因缺少 CSRF 被服务端拒绝，重试一次 */ }
    return this._csrf || "";
  },

  async me() {
    try {
      const resp = await fetch("/api/auth/me");
      if (resp.ok) return await resp.json();
    } catch (e) { /* ignore */ }
    return null;
  },

  /* 全局会话处理：401 → 登录页；强制改密 403 → 改密页（排除登录/CSRF 自身防死循环） */
  _handleAuth(resp, path) {
    if (resp.status === 401 && path !== "/api/auth/login" && path !== "/api/auth/csrf") {
      location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
      return;
    }
    if (resp.status === 403) {
      resp.json().then((d) => {
        if (d && d.code === "PASSWORD_CHANGE_REQUIRED") location.href = "/account";
      }).catch(() => {});
    }
  },
};

const Fmt = {
  num(v) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return Number(v).toLocaleString("zh-CN");
  },
  price(v) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 4 });
  },
  pct(v) {
    if (v === null || v === undefined) return "—";
    return (v > 0 ? "+" : "") + Number(v).toFixed(2) + "%";
  },
  size(bytes) {
    if (bytes === null || bytes === undefined) return "—";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1048576).toFixed(1) + " MB";
  },
  cls(v) {
    return v > 0 ? "up" : v < 0 ? "down" : "flat";
  },
  esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  },
  badge(status) {
    const map = { "完整": "ok", success: "ok", "部分成功": "warn", "部分": "warn", "异常": "bad", failed: "bad" };
    return map[status] || "warn";
  },
  /* 运维状态：统一三档（正常/警告/异常），图标+文字标识 */
  statusText(s) {
    return { ok: "正常", warn: "警告", crit: "异常" }[s] || s || "—";
  },
  statusIcon(s) {
    return { ok: "✅", warn: "⚠️", crit: "❌" }[s] || "—";
  },
  duration(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    const s = Math.round(seconds);
    if (s < 60) return s + "s";
    const m = Math.floor(s / 60), r = s % 60;
    if (m < 60) return m + "m" + String(r).padStart(2, "0") + "s";
    const h = Math.floor(m / 60);
    return h + "h" + String(m % 60).padStart(2, "0") + "m";
  },
  uptime(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (d > 0) return d + "天" + h + "小时";
    if (h > 0) return h + "小时" + m + "分";
    return m + "分钟";
  },
};

function toast(msg, kind = "info") {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.className = "toast show " + kind;
  clearTimeout(el._t);
  el._t = setTimeout(() => (el.className = "toast"), 3200);
}
