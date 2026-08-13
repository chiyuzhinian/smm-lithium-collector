/* 首页渲染：今日必看 / 指标卡 / 专题入口 / 最近更新 / 固定汇总状态 */
"use strict";

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const ov = await API.get("/api/overview");
    renderToday(ov);
    renderMetrics(ov);
    renderTopics(ov);
    renderRecent(ov);
    renderFixed(ov);
  } catch (e) {
    toast("首页数据加载失败：" + e.message, "error");
    document.getElementById("today-card").innerHTML =
      '<div class="empty-state"><div class="empty-icon">⚠️</div><div>数据加载失败，请稍后重试</div></div>';
  }
});

/* ── 今日必看 ─────────────────────────────────────────── */
function renderToday(ov) {
  const t = ov.today_must_see || {};
  const el = document.getElementById("today-card");
  if (!t.latest_date) {
    el.innerHTML = '<div class="empty-state"><div class="empty-icon">⚠️</div><div>暂无采集数据</div></div>';
    return;
  }
  const comp = t.completeness || {};
  const files = t.files || {};
  const dateDual = t.db_price_date && t.db_price_date !== t.latest_date
    ? `<span class="label-warn">价格数据日期 ${t.db_price_date}（当日价格尚未发布，文件日期为最新）</span>` : "";

  const dls = [];
  if (files.xlsx_exists) dls.push(`<a class="btn btn-primary" href="${files.xlsx_path}" download>⬇ 今日总表 Excel</a>`);
  else dls.push(`<span class="btn btn-disabled" title="文件未生成">⬇ 今日总表 Excel（未生成）</span>`);
  if (files.csv_exists) dls.push(`<a class="btn btn-outline" href="${files.csv_path}" download>⬇ 今日总表 CSV</a>`);

  el.innerHTML = `
<div class="today-main">
  <div class="today-date-block">
    <div class="today-label">最新数据日期</div>
    <div class="today-date">${Fmt.esc(t.latest_date)}</div>
    <div class="today-updated">最新更新：${Fmt.esc(files.updated_at || "—")}</div>
    ${dateDual}
  </div>
  <div class="today-completeness">
    <div class="comp-line">
      <span class="comp-num">${comp.success ?? "—"} / ${comp.expected ?? "—"}</span>
      <span class="comp-text">成功分类 / 预期分类</span>
    </div>
    <span class="status-badge ${Fmt.badge(comp.status)}">今日状态：${Fmt.esc(comp.status || "—")}</span>
    ${(comp.missing_categories || []).length ? `<div class="comp-missing">缺失：${Fmt.esc(comp.missing_categories.join("、"))}</div>` : ""}
  </div>
  <div class="today-actions">
    <div class="today-btns">${dls.join("")}</div>
    <a class="btn btn-outline" href="/today">📂 今日分类数据查看 →</a>
  </div>
</div>`;
}

/* ── 关键指标卡 ───────────────────────────────────────── */
function renderMetrics(ov) {
  const el = document.getElementById("metric-grid");
  const ms = ov.metrics || [];
  if (!ms.length) { el.innerHTML = '<div class="empty-state">暂无指标数据</div>'; return; }
  el.innerHTML = ms.map((m) => `
<div class="metric-card">
  <div class="metric-head">
    <span class="metric-name">${Fmt.esc(m.name)}</span>
    <span class="metric-sub">${Fmt.esc(m.category || "")}</span>
  </div>
  <div class="metric-value">${Fmt.price(m.value)}<span class="metric-unit">${Fmt.esc(m.unit || "")}</span></div>
  <div class="metric-change">
    <span class="${Fmt.cls(m.change_pct)}">${m.change_pct === null || m.change_pct === undefined ? "—" : "较昨日 " + Fmt.pct(m.change_pct)}</span>
  </div>
  <div class="metric-spark" data-spark="${Fmt.esc(JSON.stringify(m.sparkline || []))}"></div>
  <div class="metric-time">更新：${Fmt.esc(m.updated_at || "—")}</div>
</div>`).join("");
  el.querySelectorAll(".metric-spark").forEach((d) => {
    try { renderSparkline(d, JSON.parse(d.dataset.spark)); } catch (e) { /* ignore */ }
  });
}

/* ── 专题入口 ─────────────────────────────────────────── */
function renderTopics(ov) {
  const el = document.getElementById("topic-grid");
  const ts = ov.topics || [];
  el.innerHTML = ts.map((t) => `
<a class="topic-card${t.emphasis ? " emphasis" : ""}" href="${t.url}">
  <div class="topic-icon">${Fmt.esc(t.icon || "📊")}</div>
  <div class="topic-name">${Fmt.esc(t.name)}${t.emphasis ? '<span class="hot-tag">重点</span>' : ""}</div>
  <div class="topic-arrow">进入专题 →</div>
</a>`).join("");
}

/* ── 最近 7 天价格数据（业务日期） ───────────────────── */
function renderRecent(ov) {
  const el = document.getElementById("recent-list");
  const items = ov.recent_files || [];
  el.innerHTML = items.map((f) => `
<div class="recent-item">
  <span class="recent-date">${Fmt.esc(f.date)}</span>
  <div class="recent-info">
    <div class="recent-name" title="${Fmt.esc(f.path)}">${Fmt.esc(f.name)}</div>
    <div class="recent-meta">${Fmt.size(f.size)} · 文件更新时间 ${Fmt.esc(f.modified || "—")}</div>
  </div>
  <span class="status-badge ${Fmt.badge(f.status)}">${Fmt.esc(f.status || "—")}</span>
  <div class="recent-actions">
    ${f.xlsx_exists ? `<a class="btn btn-small" href="${f.path}" download>⬇ Excel</a>`
      : '<span class="btn btn-small btn-disabled">Excel 未生成</span>'}
    ${f.csv_exists ? `<a class="btn btn-small btn-outline" href="${f.csv_path}" download>CSV</a>` : ""}
  </div>
</div>`).join("") || '<div class="empty-state">暂无数据</div>';
}

/* ── 固定汇总状态 ─────────────────────────────────────── */
function renderFixed(ov) {
  const el = document.getElementById("fixed-summary");
  const f = ov.fixed_summary || {};
  const statusMap = { "完整": "ok", "部分": "warn", "异常": "bad", "部分成功": "warn" };
  const st = f.formal_status || "未知";
  const badgeCls = statusMap[st] || "warn";
  const warnings = f.warnings || [];
  el.innerHTML = `
<div class="fixed-card">
  <div class="fixed-status">
    <span class="status-badge ${badgeCls}">固定汇总状态：${Fmt.esc(st)}</span>
    <div class="fixed-updated">正式固定汇总更新：${Fmt.esc(f.formal_updated_at || "—")}</div>
  </div>
  <div class="fixed-links">
    ${f.formal_exists ? `<a class="btn btn-small" href="${f.formal_file_path}" download>⬇ 正式固定汇总</a>` : '<span class="btn btn-small btn-disabled">正式固定汇总未生成</span>'}
    ${f.temp_snapshot_path ? `<a class="btn btn-small btn-outline" href="/${f.temp_snapshot_path}" download>⬇ 临时快照</a>` : ""}
  </div>
  ${warnings.length ? `<div class="fixed-warnings"><ul>${warnings.map((w) => `<li>⚠️ ${Fmt.esc(w)}</li>`).join("")}</ul></div>` : ""}
</div>`;
}
