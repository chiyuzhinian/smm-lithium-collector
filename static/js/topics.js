/* 业务专题页：四链 tab（hash 路由，默认回收链）+ 指标卡 + 趋势图 + 下载入口 */
"use strict";

let TOPICS = [];
let ACTIVE = "recycling";

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const d = await API.get("/api/topics");
    TOPICS = d.topics || [];
    const hash = (location.hash || "").replace("#", "");
    if (TOPICS.some((t) => t.key === hash)) ACTIVE = hash;
    renderTabs();
    await renderTopic(ACTIVE);
  } catch (e) {
    toast("专题数据加载失败：" + e.message, "error");
  }
  window.addEventListener("hashchange", async () => {
    const key = (location.hash || "").replace("#", "");
    if (key && key !== ACTIVE && TOPICS.some((t) => t.key === key)) {
      ACTIVE = key;
      renderTabs();
      await renderTopic(key);
    }
  });
});

function renderTabs() {
  const el = document.getElementById("topic-tabs");
  el.innerHTML = TOPICS.map((t) => `
<button class="topic-tab${t.key === ACTIVE ? " active" : ""}" data-key="${Fmt.esc(t.key)}"
        style="${t.key === ACTIVE ? `--tab-color:${t.color}` : ""}">
  ${Fmt.esc(t.icon || "")} ${Fmt.esc(t.name)}${t.emphasis ? '<span class="hot-tag">重点</span>' : ""}
</button>`).join("");
  el.querySelectorAll(".topic-tab").forEach((b) =>
    b.addEventListener("click", () => { location.hash = b.dataset.key; }));
}

async function renderTopic(key) {
  const t = TOPICS.find((x) => x.key === key);
  const el = document.getElementById("topic-content");
  if (!t) { el.innerHTML = '<div class="empty-state">专题不存在</div>'; return; }

  el.innerHTML = `
<div class="topic-desc" style="border-left-color:${t.color}">
  <div class="topic-desc-title">${Fmt.esc(t.icon || "")} ${Fmt.esc(t.name)} · 业务说明</div>
  <p>${Fmt.esc(t.description || "")}</p>
</div>

<div class="metric-grid" id="tp-metrics"></div>

<div class="chart-card" data-chart>
  <div class="chart-title">${Fmt.esc(t.name)} · 关键产品近30日趋势</div>
  <div class="chart-box" id="tp-chart"></div>
</div>

<div class="two-col">
  <section class="chart-card">
    <div class="chart-title">今日相关文件下载</div>
    <div class="file-links" id="tp-files"></div>
  </section>
  <section class="chart-card" id="tp-rank-card" style="display:${t.emphasis ? "block" : "none"}">
    <div class="chart-title">回收链涨跌榜</div>
    <div class="rank-wrap" id="tp-rank"></div>
  </section>
</div>`;

  // 产品指标卡
  const me = el.querySelector("#tp-metrics");
  const products = t.products || [];
  me.innerHTML = products.map((p) => {
    const sparkVals = (p.spark_points || []).map((x) => x.value);
    return `
<div class="metric-card">
  <div class="metric-head"><span class="metric-name">${Fmt.esc(p.product)}</span><span class="metric-sub">${Fmt.esc(p.category || "")}</span></div>
  <div class="metric-value">${Fmt.price(p.value)}<span class="metric-unit">${Fmt.esc(p.unit || "")}</span></div>
  <div class="metric-change"><span class="${Fmt.cls(p.change_pct)}">${p.change_pct == null ? "—" : "较上次 " + (p.prev_date ? "(" + String(p.prev_date).slice(5) + ") " : "") + Fmt.pct(p.change_pct)}</span></div>
  <div class="metric-spark" data-spark="${Fmt.esc(JSON.stringify(sparkVals))}"></div>
  <div class="metric-time">数据日期：${Fmt.esc(p.price_date || "—")}</div>
</div>`;
  }).join("") || '<div class="empty-state">暂无产品数据</div>';
  me.querySelectorAll(".metric-spark").forEach((d) => {
    try { renderSparkline(d, JSON.parse(d.dataset.spark)); } catch (e) { /* ignore */ }
  });

  // 文件下载入口
  const fe = el.querySelector("#tp-files");
  const files = t.files || [];
  fe.innerHTML = files.map((f) => `
<div class="file-link-item">
  <span class="file-link-cat">${Fmt.esc(f.category)}</span>
  <a class="btn btn-small" href="${f.path}" download>⬇ ${Fmt.esc(f.date)} CSV</a>
</div>`).join("") || '<div class="empty-state">暂无文件</div>';

  // 涨跌榜（仅回收链）
  if (t.emphasis && t.rankings) {
    renderRankTable(el.querySelector("#tp-rank"), t.rankings.top_gainers,
                    t.rankings.top_losers, { based_on: t.rankings.based_on });
  }

  // 主趋势图：拉取每个产品的趋势序列（最多 6 条）
  await renderMainChart(el.querySelector("#tp-chart"), t, products);
}

async function renderMainChart(el, t, products) {
  const series = [];
  let xData = [];
  for (const p of products.slice(0, 6)) {
    try {
      const d = await API.get("/api/trends?product=" + encodeURIComponent(p.product) + "&days=30");
      const s = (d.series || [])[0];
      if (!s) continue;
      const pts = s.points || [];
      const data = pts.map((x) => x.average_price);
      if (xData.length === 0) xData = pts.map((x) => x.price_date);
      series.push({ name: p.product, data });
    } catch (e) { /* 单产品失败不影响整体 */ }
  }
  renderLine(el, xData, series, { colors: [t.color, "#2563eb", "#16a34a", "#f59e0b", "#7c3aed", "#0891b2"] });
}
