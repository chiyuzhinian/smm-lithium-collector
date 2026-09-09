/* 每日报价：40 个 SMM 业务品种最新报价（as_of 日期语义 + 展开详情） */
"use strict";

const qstate = {
  products: [],   // 全量产品（含 searchable）
  rows: [],       // 当前筛选后的报价行
  q: "",
  org: "",
  cat: "",
  asOf: "",
  filters: {},
  expanded: new Set(),
};

const STATUS_TAG = {
  confirmed: "",
  confirmed_renamed: '<span class="pnote pnote-warn">口径已调整</span>',
  unavailable_merged: '<span class="pnote pnote-up">暂无独立报价</span>',
};

const FREQ_TAG = {
  daily: '<span class="tag daily">日频</span>',
  weekly: '<span class="tag weekly">周报价</span>',
};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  bindFilters();
  try {
    const data = await API.get("/api/portal/products", 60000);
    qstate.products = data.products.filter((p) => p.source === "SMM");
    qstate.filters = data.filters;
    fillSelects(data);
    setAsOfBounds(data.meta);
    document.getElementById("st-range").textContent = data.meta.history_from || "—";
    if (data.meta.load_errors && data.meta.load_errors.length) {
      notify("映射配置存在问题：" + data.meta.load_errors[0], "error");
    }
  } catch (err) {
    document.getElementById("table-scroll").innerHTML =
      emptyHtml("⚠️", "业务品种映射加载失败：" + err.message);
    return;
  }
  await refresh();
}

function bindFilters() {
  document.getElementById("q-input").addEventListener("input", (e) => {
    qstate.q = e.target.value.trim();
    renderSuggest();
  });
  document.getElementById("q-input").addEventListener("focus", () => renderSuggest(true));
  document.addEventListener("click", (e) => {
    const s = document.getElementById("q-suggest");
    if (s && !e.target.closest(".search-wrap")) s.classList.remove("open");
  });
  document.getElementById("org-select").addEventListener("change", async (e) => {
    qstate.org = e.target.value;
    await refresh();
  });
  document.getElementById("cat-select").addEventListener("change", async (e) => {
    qstate.cat = e.target.value;
    await refresh();
  });
  document.getElementById("asof-input").addEventListener("change", async (e) => {
    qstate.asOf = e.target.value;
    await refresh();
  });
  document.getElementById("reset-btn").addEventListener("click", () => {
    qstate.q = ""; qstate.org = ""; qstate.cat = ""; qstate.asOf = "";
    document.getElementById("q-input").value = "";
    document.getElementById("org-select").value = "";
    document.getElementById("cat-select").value = "";
    document.getElementById("asof-input").value = "";
    document.getElementById("q-suggest").classList.remove("open");
    refresh();
  });
  document.getElementById("btn-trends").addEventListener("click", (e) => {
    const ids = qstate.rows.map((r) => r.id);
    if (ids.length) {
      e.preventDefault();
      location.href = "/trends?ids=" + encodeURIComponent(ids.slice(0, 6).join(","));
    }
  });
}

function fillSelects(data) {
  const orgSel = document.getElementById("org-select");
  for (const o of data.filters.organizations || []) {
    const op = document.createElement("option");
    op.value = o; op.textContent = o;
    orgSel.appendChild(op);
  }
  const catSel = document.getElementById("cat-select");
  for (const c of data.filters.info_categories || []) {
    const op = document.createElement("option");
    op.value = c; op.textContent = c;
    catSel.appendChild(op);
  }
}

function setAsOfBounds(meta) {
  const inp = document.getElementById("asof-input");
  if (meta.history_from) inp.min = meta.history_from;
  if (meta.db_latest_date) inp.max = meta.db_latest_date;
}

/* ── 产品搜索下拉 ── */

function searchProducts() {
  const q = qstate.q.toLowerCase();
  if (!q) return qstate.products;
  return qstate.products.filter((p) => (p.searchable || "").toLowerCase().includes(q));
}

function renderSuggest(forceOpen) {
  const box = document.getElementById("q-suggest");
  const q = qstate.q;
  if (!q) { box.classList.remove("open"); return; }
  const hits = searchProducts().slice(0, 8);
  if (hits.length === 0) {
    box.innerHTML = `<div class="suggest-empty">没有匹配的产品</div>`;
  } else {
    box.innerHTML = hits.map((p) => `
      <button type="button" class="suggest-item" data-id="${escHtml(p.id)}">
        <div class="suggest-name">${escHtml(p.display_name)}
          ${p.spec ? `<span style="font-weight:400;color:var(--ink-3)"> · ${escHtml(p.spec.slice(0, 40))}${p.spec.length > 40 ? "…" : ""}</span>` : ""}
        </div>
        <div class="suggest-spec">${escHtml(p.organization)} · ${escHtml(p.info_category)} · SMM ${escHtml((p.series[0] || {}).product_name || "")}</div>
      </button>`).join("");
    box.querySelectorAll(".suggest-item").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.dataset.id;
        qstate.q = "";
        document.getElementById("q-input").value = "";
        box.classList.remove("open");
        expandRow(id);
        document.getElementById("row-" + id)?.scrollIntoView({ block: "center", behavior: "smooth" });
      });
    });
  }
  box.classList.add("open");
}

/* ── 数据加载 ── */

async function refresh() {
  const scroll = document.getElementById("table-scroll");
  scroll.innerHTML = loadingHtml("正在加载最新报价…");
  const params = new URLSearchParams();
  if (qstate.asOf) params.set("as_of", qstate.asOf);
  if (qstate.org) params.set("org", qstate.org);
  if (qstate.cat) params.set("info_category", qstate.cat);
  try {
    const data = await API.get("/api/portal/quotes?" + params.toString(), 15000);
    qstate.rows = data.rows;
    updateStatus(data);
    renderTable();
  } catch (err) {
    scroll.innerHTML = emptyHtml("⚠️", "报价加载失败：" + err.message);
  }
}

function updateStatus(data) {
  const meta = data.meta || {};
  document.getElementById("st-latest").textContent = meta.db_latest_date || "—";
  let latestCollected = "—";
  for (const r of data.rows) {
    if (r.quote && r.quote.collected_at > latestCollected) latestCollected = r.quote.collected_at;
  }
  document.getElementById("st-collected").textContent = latestCollected === "—" ? "—" : fmtTs(latestCollected);
  document.getElementById("st-count").textContent = data.rows.length;
  document.getElementById("result-count").innerHTML =
    `共 <b>${data.rows.length}</b> 个业务品种` +
    (qstate.asOf ? ` · 截至 <b class="num">${escHtml(qstate.asOf)}</b> 的最新报价` : " · 最新可用报价");
}

/* ── 渲染 ── */

function renderTable() {
  const scroll = document.getElementById("table-scroll");
  if (qstate.rows.length === 0) {
    scroll.innerHTML = emptyHtml("📭", "没有符合条件的品种，请调整筛选条件");
    return;
  }
  const head = `
  <table class="data-table">
    <thead><tr>
      <th>业务产品 / 规格</th>
      <th class="num-cell">最低价</th>
      <th class="num-cell">最高价</th>
      <th class="num-cell">日均价</th>
      <th class="num-cell">涨跌</th>
      <th class="ctr">单位</th>
      <th class="num-cell">报价日期</th>
      <th class="num-cell">采集更新</th>
      <th class="ctr">频次</th>
    </tr></thead>
    <tbody id="quotes-tbody">${qstate.rows.map(rowHtml).join("")}</tbody>
  </table>`;
  scroll.innerHTML = head;
  scroll.querySelectorAll(".expand-btn").forEach((btn) => {
    btn.addEventListener("click", () => toggleRow(btn.dataset.id));
  });
}

function rowHtml(r) {
  const q = r.quote;
  const statusTag = STATUS_TAG[r.mapping_status] || "";
  const freqTag = FREQ_TAG[r.frequency] || "";
  const noQuote = r.mapping_status === "unavailable_merged";
  /* 第二层：规格 · 组织 · 类别（空规格不占行、不显示「—」） */
  const subParts = [];
  if (r.spec) subParts.push(r.spec);
  subParts.push(`${r.organization} · ${r.info_category}`);
  const subLine = `<div class="product-spec">${escHtml(subParts.join(" · "))}</div>`;
  const cells = noQuote
    ? `<td class="num-cell" colspan="4"><span style="color:var(--ink-3)">暂无独立报价（口径说明见展开详情）</span></td>`
    : `
      <td class="num-cell">${q ? priceText(q.min_price) : "—"}</td>
      <td class="num-cell">${q ? priceText(q.max_price) : "—"}</td>
      <td class="num-cell avg-cell">${q ? priceText(q.average_price) : "—"}</td>
      <td class="num-cell change-cell">${changeBadge(q ? q.change_value : null)}</td>`;
  return `
  <tr id="row-${escHtml(r.id)}" class="${noQuote ? "row-blank" : ""}">
    <td class="product-cell">
      <div class="product-name-row">
        <button class="expand-btn" data-id="${escHtml(r.id)}" aria-label="展开详情">▸</button>
        <span class="product-name">${escHtml(r.display_name)}</span>
        <span class="pname-tags">${statusTag}</span>
      </div>
      ${subLine}
    </td>
    ${cells}
    <td class="ctr">${escHtml(q ? q.unit : r.unit)}</td>
    <td class="num-cell">${q ? q.price_date : "—"}</td>
    <td class="num-cell" style="color:var(--ink-3)">${q ? fmtTs(q.collected_at) : "—"}</td>
    <td class="ctr">${freqTag}</td>
  </tr>
  <tr class="detail-row" id="detail-${escHtml(r.id)}" hidden>
    <td class="detail-panel" colspan="9">${detailHtml(r)}</td>
  </tr>`;
}

function changeBadge(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(String(v).replace(/,/g, ""));
  const cls = n > 0 ? "up" : n < 0 ? "down" : "flat";
  const grouped = groupPrice(v);
  const text = n > 0 ? "+" + grouped : grouped;
  return `<span class="change-badge ${cls}">${escHtml(text)}</span>`;
}

function detailHtml(r) {
  const q = r.quote;
  const smmSeries = (r.series || []).map((t) =>
    `${escHtml(t.category)} / ${escHtml(t.product_name)}${t.specification ? " / " + escHtml(t.specification) : ""}`).join("；");
  const noteCls = r.mapping_status === "unavailable_merged" ? "" : " info";
  const noteIcon = r.mapping_status === "unavailable_merged" ? "⚠️" : "ℹ️";
  const quoteLine = q
    ? `<div class="detail-item"><span class="k">当前报价（同一记录）</span>
         <span class="v num">最低 ${priceText(q.min_price)} · 最高 ${priceText(q.max_price)} ·
         日均价 ${priceText(q.average_price)} · 涨跌 ${priceText(q.change_value)} ${escHtml(q.unit)}
         · 报价日期 ${q.price_date}</span></div>`
    : `<div class="detail-item"><span class="k">当前报价</span><span class="v">暂无独立报价</span></div>`;
  return `
  <div class="detail-grid">
    <div class="detail-item"><span class="k">完整业务规格</span>
      <span class="v">${r.template_detail ? escHtml(r.template_detail) : "—"}</span></div>
    <div class="detail-item"><span class="k">SMM 原始绑定（精确三元组）</span>
      <span class="v">${smmSeries || "无绑定（无独立报价）"}</span></div>
    <div class="detail-item"><span class="k">数据来源</span>
      <span class="v">${escHtml(r.source)} · ${escHtml((q && q.market) || "SMM锂电现货")} · 采集分类 ${escHtml((q && q.category) || (r.series[0] || {}).category || "—")}</span></div>
    <div class="detail-item"><span class="k">映射依据</span><span class="v">${escHtml(r.mapping_basis || "—")}</span></div>
    ${quoteLine}
    <div class="detail-item"><span class="k">周报价说明</span>
      <span class="v">${r.frequency === "weekly" ? "该品种为周报价：日均价为对应发布周期的 SMM 均价，按实际报价日期展示，不代表每日独立发布" : "日频报价：按 SMM 发布日逐日采集"}</span></div>
    ${r.note ? `<div class="detail-note ${noteCls}">${noteIcon} ${escHtml(r.note)}</div>` : ""}
    <div class="detail-actions">
      <a class="btn sm primary" href="/trends?ids=${encodeURIComponent(r.id)}">查看走势 →</a>
      <span style="font-size:12px;color:var(--ink-4);align-self:center">走势与月报使用同一报价系列绑定</span>
    </div>
  </div>`;
}

function toggleRow(id) {
  const detail = document.getElementById("detail-" + id);
  const row = document.getElementById("row-" + id);
  const btn = row.querySelector(".expand-btn");
  if (detail.hidden) {
    qstate.expanded.add(id);
    detail.hidden = false;
    row.classList.add("expanded");
    btn.textContent = "▾";
  } else {
    qstate.expanded.delete(id);
    detail.hidden = true;
    row.classList.remove("expanded");
    btn.textContent = "▸";
  }
}

function expandRow(id) {
  const detail = document.getElementById("detail-" + id);
  if (detail && detail.hidden) toggleRow(id);
}
