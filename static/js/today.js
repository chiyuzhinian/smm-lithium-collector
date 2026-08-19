/* 今日价格页：总表下载 / 完整性 / 板块指标 / 涨跌榜 / 分类文件 */
"use strict";

let CAT_DATA = null;
let CURRENT_GROUP = "all";

document.addEventListener("DOMContentLoaded", async () => {
  try {
    CAT_DATA = await API.get("/api/latest");
    renderHead(CAT_DATA);
    renderMetrics(CAT_DATA);
    renderRanks(CAT_DATA);
    renderGroupChips(CAT_DATA);
    renderCatGrid(CAT_DATA);
    bindFilter();
  } catch (e) {
    toast("今日数据加载失败：" + e.message, "error");
  }
});

function renderHead(d) {
  const files = d.files || {};
  document.getElementById("page-date").innerHTML =
    `数据文件日期：<b>${Fmt.esc(d.date)}</b>` +
    (d.db_price_date && d.db_price_date !== d.date
      ? `<span class="label-warn">价格数据日期 ${Fmt.esc(d.db_price_date)}（当日价格尚未发布）</span>` : "");
  const btns = [];
  if (files.xlsx_exists) btns.push(`<a class="btn btn-primary" href="${files.xlsx_path}" download>⬇ 今日总表 Excel</a>`);
  else btns.push(`<span class="btn btn-disabled">今日总表 Excel 未生成</span>`);
  if (files.csv_exists) btns.push(`<a class="btn btn-outline" href="${files.csv_path}" download>⬇ 今日总表 CSV</a>`);
  document.getElementById("page-actions").innerHTML = btns.join("");

  const c = d.completeness || {};
  document.getElementById("completeness-banner").innerHTML = `
<div class="comp-banner">
  <span class="status-badge ${Fmt.badge(c.status)}">采集状态：${Fmt.esc(c.status || "—")}</span>
  <span>成功 <b>${c.success ?? "—"}</b> / 预期 <b>${c.expected ?? "—"}</b> 分类，失败 <b>${c.failed ?? "—"}</b></span>
  ${(c.missing_categories || []).length ? `<span class="comp-missing">缺失：${Fmt.esc(c.missing_categories.join("、"))}</span>` : ""}
</div>`;
}

function renderMetrics(d) {
  const el = document.getElementById("metric-grid");
  const ms = d.metrics || [];
  el.innerHTML = ms.map((m) => `
<div class="metric-card">
  <div class="metric-head"><span class="metric-name">${Fmt.esc(m.name)}</span><span class="metric-sub">${Fmt.esc(m.category || "")}</span></div>
  <div class="metric-value">${Fmt.price(m.value)}<span class="metric-unit">${Fmt.esc(m.unit || "")}</span></div>
  <div class="metric-change"><span class="${Fmt.cls(m.change_pct)}">${m.change_pct == null ? "—" : "较上次 " + (m.prev_date ? "(" + String(m.prev_date).slice(5) + ") " : "") + Fmt.pct(m.change_pct)}</span></div>
  <div class="metric-time">数据日期：${Fmt.esc(m.price_date || "—")}${m.is_stale ? '<span class="label-warn">数据较旧</span>' : ""} · 更新时间：${Fmt.esc(m.updated_at || "—")}</div>
</div>`).join("") || '<div class="empty-state">暂无指标数据</div>';
}

function renderRanks(d) {
  const r = d.rankings || {};
  renderRankTable(document.getElementById("rank-wrap"), r.top_gainers, r.top_losers, { based_on: r.based_on });
}

function renderGroupChips(d) {
  const groups = new Map();
  (d.categories || []).forEach((c) => {
    if (!groups.has(c.group)) groups.set(c.group, { key: c.group, name: c.group_name || c.group, icon: c.group_icon || "" });
  });
  const el = document.getElementById("group-chips");
  el.innerHTML = `<span class="filter-tag active" data-group="all">全部</span>` +
    [...groups.values()].map((g) =>
      `<span class="filter-tag" data-group="${Fmt.esc(g.key)}">${Fmt.esc(g.icon)} ${Fmt.esc(g.name)}</span>`).join("");
}

function renderCatGrid(d) {
  const el = document.getElementById("cat-file-grid");
  const kw = (document.getElementById("cat-search").value || "").trim().toLowerCase();
  const cats = (d.categories || []).filter((c) => {
    const okGroup = CURRENT_GROUP === "all" || c.group === CURRENT_GROUP;
    const okKw = !kw || c.category.toLowerCase().includes(kw) || (c.group_name || "").toLowerCase().includes(kw);
    return okGroup && okKw;
  });
  el.innerHTML = cats.map((c) => `
<div class="cat-file-card${c.exists ? "" : " missing"}">
  <div class="cat-file-head">
    <span class="cat-file-name">${Fmt.esc(c.category)}</span>
    <span class="cat-file-group">${Fmt.esc(c.group_icon || "")} ${Fmt.esc(c.group_name || "")}</span>
  </div>
  <div class="cat-file-body">
    ${c.exists
      ? `<a class="btn btn-small" href="${c.file_path}" download>⬇ 分类 CSV</a>`
      : `<span class="label-warn">当日文件缺失</span>`}
  </div>
</div>`).join("") || '<div class="empty-state">无匹配分类</div>';
}

function bindFilter() {
  document.getElementById("group-chips").addEventListener("click", (e) => {
    const t = e.target.closest(".filter-tag");
    if (!t) return;
    CURRENT_GROUP = t.dataset.group;
    document.querySelectorAll("#group-chips .filter-tag").forEach((x) => x.classList.toggle("active", x === t));
    renderCatGrid(CAT_DATA);
  });
  document.getElementById("cat-search").addEventListener("input", () => renderCatGrid(CAT_DATA));
}
