/* 共享前端工具：请求封装 / 格式化 / 安全转义 / 提示（新版三栏目 + 登录体系共用） */
"use strict";

/* ── 请求封装 ── */

async function apiGet(url) {
  const resp = await fetch(url, { headers: { Accept: "application/json" } });
  if (resp.status === 401) {
    location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
    throw new Error("未登录");
  }
  if (!resp.ok) throw new Error("请求失败 " + resp.status);
  return resp.json();
}

/* ── 格式化 ── */

function escHtml(s) {
  const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  return String(s ?? "").replace(/[&<>"']/g, (c) => map[c]);
}

function fmtTs(iso) {
  if (!iso) return "—";
  const m = String(iso).match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/);
  return m ? `${m[1]} ${m[2]}` : String(iso).slice(0, 16);
}

/* 价格文本：直接展示源精度（服务端已按源小数位格式化），缺失显示 —；
   千分位只给整数部分加，保留小数位与尾零（元/Wh 等精度不丢） */
function groupPrice(v) {
  if (v === null || v === undefined || v === "") return "—";
  const s = String(v).trim();
  const m = s.match(/^(-?)([\d,]+)(\.\d*)?$/);
  if (!m) return s;
  return m[1] + m[2].replace(/,/g, "").replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (m[3] || "");
}

function priceText(v) {
  return groupPrice(v);
}

/* 涨跌文本与样式：源字段原样展示（整数部分千分位），正负号保留；涨红跌绿 */
function changeText(v) {
  if (v === null || v === undefined || v === "") return { text: "—", cls: "change-flat", sign: 0 };
  const n = Number(String(v).replace(/,/g, ""));
  if (!isFinite(n)) return { text: String(v), cls: "change-flat", sign: 0 };
  const grouped = groupPrice(v);
  const text = n > 0 ? `+${grouped}` : grouped;
  return { text, cls: n > 0 ? "change-up" : n < 0 ? "change-down" : "change-flat", sign: Math.sign(n) };
}

/* 月份标签：YYYY-MM → YYYY年M月 */
function monthLabel(ym) {
  const m = String(ym).match(/^(\d{4})-(\d{2})$/);
  return m ? `${m[1]}年${parseInt(m[2], 10)}月` : ym;
}

/* ── 提示（复用 api.js 的 toast 渲染，kind: info|error） ── */

function notify(msg, kind = "info") {
  if (typeof toast === "function") toast(msg, kind);
  else console.warn(msg);
}

/* ── 空态 / 加载 ── */

function emptyHtml(icon, text) {
  return `<div class="empty-state"><div class="empty-icon">${icon}</div><div>${escHtml(text)}</div></div>`;
}

function loadingHtml(text) {
  return `<div class="loading"><span class="spinner"></span>${escHtml(text || "加载中…")}</div>`;
}

/* ── 用户产品选择偏好（三页独立、每用户独立；服务端持久化，跨刷新/重登/设备恢复） ── */

const Prefs = {
  /* 读取某页已保存的选择：返回 null（从未保存，走页面默认行为）或 id 数组（可为空=显式清空）。
     失败时提示并降级为 null，绝不阻塞页面加载。 */
  async load(page) {
    try {
      const data = await API.get("/api/user/preferences", 0); // ttl=0 禁用缓存，保证读到最新
      return data.preferences ? data.preferences[page] : null;
    } catch (err) {
      notify("产品选择加载失败，已使用默认选择", "error");
      return null;
    }
  },
  /* 保存某页选择（登录 + CSRF）；失败时 reject，由调用方 toast（不阻塞页面查询）。 */
  save(page, ids) {
    return API.post("/api/user/preferences", { page, ids });
  },
};
