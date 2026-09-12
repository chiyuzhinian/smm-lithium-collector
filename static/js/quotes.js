/* 每日报价：SMM 业务品种最新报价（as_of 日期语义 + 展开详情）
   V5：删除组织下拉；分类前移 + 产品多选（chips 跨类别累积）；
   主列显示 SMM 原始产品名+规格，业务名作副行小字。
   V7：产品选择器接入全量采集目录（/api/portal/catalog/products）；
   默认无选择 = 40 条 SMM 业务品种；多选后切换为 catalog 报价。 */
"use strict";

const qstate = {
  products: [],      // 40 条 SMM 业务产品（默认展示 + 搜索高亮）
  catalog: [],       // DB 全量产品目录（产品选择器下拉）
  catalogById: {},   // id → catalog product
  rows: [],          // 当前展示行（business quotes 或 catalog quotes）
  visible: [],       // 实际渲染的行（rows 经 picked 过滤后）
  q: "",
  cat: "",
  picked: [],        // 有序 catalog id（无选择 = 默认业务产品）
  asOf: "",
  filters: {},
  expanded: new Set(),
};

const STATUS_TAG = {
  confirmed: "",
  confirmed_renamed: '<span class="pnote pnote-warn">口径已调整</span>',
  unavailable_merged: '<span class="pnote pnote-up">暂无数据</span>',
};

const FREQ_TAG = {
  daily: '<span class="tag daily">日频</span>',
  weekly: '<span class="tag weekly">周报价</span>',
};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  bindFilters();
  // 业务产品（默认展示 + 搜索）
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
  // 全量采集产品目录（下拉选择器）
  try {
    const catData = await API.get("/api/portal/catalog/products", 60000);
    qstate.catalog = catData.products || [];
    qstate.catalogById = {};
    for (const p of qstate.catalog) qstate.catalogById[p.id] = p;
    // 同步分类下拉为全量分类（首次加载完成后再切换标签提示）
    fillCatalogSelect(catData.categories || []);
    document.getElementById("prod-suggest-counter").textContent =
      `${qstate.catalog.length} 个全量产品可选`;
  } catch (err) {
    notify("全量产品目录加载失败：" + err.message, "error");
  }
  await refresh();
}

function bindFilters() {
  document.getElementById("q-input").addEventListener("input", (e) => {
    qstate.q = e.target.value.trim();
    renderSuggest();
  });
  document.getElementById("q-input").addEventListener("focus", () => renderSuggest(true));
  document.getElementById("prod-pick").addEventListener("click", (e) => {
    e.stopPropagation();
    toggleProdSuggest();
  });
  // 产品选择器内搜索
  document.getElementById("prod-search-input").addEventListener("input", () => {
    renderProdSuggestList();
  });
  document.getElementById("prod-search-input").addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", (e) => {
    const s = document.getElementById("q-suggest");
    if (s && !e.target.closest(".search-wrap")) s.classList.remove("open");
    const p = document.getElementById("prod-suggest");
    if (p && !e.target.closest("#prod-pick") && !e.target.closest("#prod-suggest")) {
      p.hidden = true;
    }
  });
  document.getElementById("cat-select").addEventListener("change", async (e) => {
    qstate.cat = e.target.value;
    if (qstate.picked.length === 0) await refresh();
  });
  document.getElementById("asof-input").addEventListener("change", async (e) => {
    qstate.asOf = e.target.value;
    await refresh();
  });
  document.getElementById("reset-btn").addEventListener("click", () => {
    qstate.q = ""; qstate.cat = ""; qstate.picked = []; qstate.asOf = "";
    document.getElementById("q-input").value = "";
    document.getElementById("cat-select").value = "";
    document.getElementById("asof-input").value = "";
    document.getElementById("prod-search-input").value = "";
    document.getElementById("q-suggest").classList.remove("open");
    document.getElementById("prod-suggest").hidden = true;
    renderPickedChips();
    refresh();
  });
  document.getElementById("btn-trends").addEventListener("click", (e) => {
    const ids = qstate.picked.length
      ? qstate.picked.filter((id) => !id.startsWith("cp_"))  // 仅传递业务产品 id
      : qstate.visible.map((r) => r.id);
    if (ids.length) {
      e.preventDefault();
      location.href = "/trends?ids=" + encodeURIComponent(ids.join(","));
    } else if (qstate.picked.length) {
      // 仅 catalog 产品被选 → 趋势页暂不支持
      e.preventDefault();
      notify("所选为全量采集产品（不在业务映射内），暂不支持价格走势", "info");
    }
  });
}

function fillSelects(data) {
  const catSel = document.getElementById("cat-select");
  for (const c of data.filters.info_categories || []) {
    const op = document.createElement("option");
    op.value = c; op.textContent = c;
    catSel.appendChild(op);
  }
}

function fillCatalogSelect(categories) {
  // 业务 info_categories 保持原样；不影响 catalog（catalog 按自身 category 分组渲染）
  // 保留此函数以备后续：若需要切换 cat-select 为全量分类，可在此扩展
  void categories;
}

function setAsOfBounds(meta) {
  const inp = document.getElementById("asof-input");
  if (meta.history_from) inp.min = meta.history_from;
  if (meta.db_latest_date) inp.max = meta.db_latest_date;
}

function toggleProdSuggest() {
  const box = document.getElementById("prod-suggest");
  box.hidden = !box.hidden;
  if (!box.hidden) {
    const inp = document.getElementById("prod-search-input");
    if (inp) inp.focus();
    renderProdSuggestList();
  }
}

/* ── 业务产品搜索下拉（q-input 高亮） ── */

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

/* ── 全量产品选择下拉（catalog） ── */

function renderProdSuggestList() {
  const list = document.getElementById("prod-suggest-list");
  if (!list) return;
  const q = (document.getElementById("prod-search-input") || {}).value || "";
  const norm = q.toLowerCase();
  const hits = qstate.catalog.filter((p) => !norm || (p.searchable || "").includes(norm));
  if (!hits.length) {
    list.innerHTML = `<div class="suggest-empty">没有匹配的产品</div>`;
    return;
  }
  // 按 category 分组
  const byCat = {};
  for (const p of hits) {
    if (!byCat[p.category]) byCat[p.category] = [];
    byCat[p.category].push(p);
  }
  const sortedCats = Object.keys(byCat).sort((a, b) => {
    if (a === qstate.cat) return -1;
    if (b === qstate.cat) return 1;
    return a.localeCompare(b, "zh");
  });
  list.innerHTML = sortedCats.map((cat) => `
    <div class="suggest-group">
      <div class="suggest-group-title">${escHtml(cat)} <span class="muted">(${byCat[cat].length})</span></div>
      ${byCat[cat].map((p) => {
        const picked = qstate.picked.includes(p.id);
        return `<button type="button" class="suggest-item" data-id="${escHtml(p.id)}">
          <div class="suggest-name">${picked ? "✓ " : ""}${escHtml(p.product_name)}
            ${p.specification ? `<span class="muted"> · ${escHtml(p.specification.slice(0, 32))}${p.specification.length > 32 ? "…" : ""}</span>` : ""}
            <span class="muted right">${escHtml(p.unit)}</span></div>
          <div class="suggest-spec">${escHtml(p.source)} · ${escHtml(p.category)}</div>
        </button>`;
      }).join("")}
    </div>
  `).join("");
  list.querySelectorAll(".suggest-item").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      togglePicked(btn.dataset.id);
    });
  });
}

function togglePicked(id) {
  const i = qstate.picked.indexOf(id);
  if (i >= 0) qstate.picked.splice(i, 1);
  else qstate.picked.push(id);
  renderPickedChips();
  renderProdSuggestList();
  refresh();
}

function renderPickedChips() {
  const row = document.getElementById("picked-chips");
  row.innerHTML = qstate.picked.map((id) => {
    const p = qstate.catalogById[id] || qstate.products.find((x) => x.id === id);
    if (!p) return "";
    const name = p.display_label
      || (p.spec ? `${p.display_name}（${p.spec}）` : p.display_name)
      || `${p.product_name}${p.specification ? "（" + p.specification + "）" : ""}`;
    return `<span class="legend-chip">
      <span class="chip-name" title="${escHtml(name)}">${escHtml(name)}</span>
      <button class="legend-remove" data-id="${escHtml(id)}" aria-label="移除 ${escHtml(name)}">×</button>
    </span>`;
  }).join("");
  row.querySelectorAll(".legend-remove").forEach((btn) => {
    btn.addEventListener("click", () => togglePicked(btn.dataset.id));
  });
}

/* ── 数据加载 ── */

async function refresh() {
  const scroll = document.getElementById("table-scroll");
  scroll.innerHTML = loadingHtml("正在加载最新报价…");
  try {
    if (qstate.picked.length > 0) {
      // 用户多选 → 走 catalog 报价接口
      const params = new URLSearchParams();
      params.set("ids", qstate.picked.join(","));
      if (qstate.asOf) params.set("as_of", qstate.asOf);
      const data = await API.get("/api/portal/catalog/quotes?" + params.toString(), 15000);
      qstate.rows = (data.rows || []).map((r) => ({ ...r, __catalog: true }));
    } else {
      // 默认 → 业务映射最新报价
      const params = new URLSearchParams();
      if (qstate.asOf) params.set("as_of", qstate.asOf);
      if (qstate.cat) params.set("info_category", qstate.cat);
      const data = await API.get("/api/portal/quotes?" + params.toString(), 15000);
      qstate.rows = data.rows || [];
    }
    updateStatus();
    qstate.visible = qstate.rows;
    renderTable();
  } catch (err) {
    scroll.innerHTML = emptyHtml("⚠️", "报价加载失败：" + err.message);
  }
}

function updateStatus() {
  const meta = qstate.rows && qstate.rows[0] ? (qstate.rows[0].quote || {}) : {};
  const latestDate = qstate.rows.reduce((m, r) => {
    const d = (r.quote && r.quote.price_date) || "";
    return d > m ? d : m;
  }, "");
  const latestCollected = qstate.rows.reduce((m, r) => {
    const t = (r.quote && r.quote.collected_at) || "";
    return t > m ? t : m;
  }, "");
  document.getElementById("st-latest").textContent = latestDate || "—";
  document.getElementById("st-collected").textContent = latestCollected ? fmtTs(latestCollected) : "—";
  document.getElementById("st-count").textContent = qstate.visible.length;
  document.getElementById("result-count").innerHTML =
    (qstate.picked.length
      ? `已选 <b>${qstate.picked.length}</b> 个全量产品`
      : `共 <b>${qstate.visible.length}</b> 个业务品种`) +
    (qstate.asOf ? ` · 截至 <b class="num">${escHtml(qstate.asOf)}</b> 的最新报价` : " · 最新可用报价");
}

/* ── 渲染 ── */

function renderTable() {
  const scroll = document.getElementById("table-scroll");
  if (qstate.visible.length === 0) {
    const msg = qstate.picked.length
      ? "所选产品暂无报价数据，请调整选择"
      : "没有符合条件的品种，请调整筛选条件";
    scroll.innerHTML = emptyHtml("📭", msg);
    return;
  }
  const head = `
  <table class="data-table">
    <thead><tr>
      <th>SMM 产品 / 规格</th>
      <th class="num-cell">最低价</th>
      <th class="num-cell">最高价</th>
      <th class="num-cell">日均价</th>
      <th class="num-cell">涨跌</th>
      <th class="ctr">单位</th>
      <th class="num-cell">报价日期</th>
      <th class="num-cell">采集更新</th>
      <th class="ctr">频次</th>
    </tr></thead>
    <tbody id="quotes-tbody">${qstate.visible.map((r) => r.__catalog ? rowHtmlCatalog(r) : rowHtml(r)).join("")}</tbody>
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
  /* 主行 = SMM 原始产品名 + 规格（与采集库一致，不手工简化）；
     副行 = 业务名 · 业务规格 · 板块 · 类别 */
  const smmName = (q && q.product_name) || (r.series[0] || {}).product_name || "";
  const smmSpec = (q && q.specification) || (r.series[0] || {}).specification || "";
  const primary = smmName + (smmSpec ? ` / ${smmSpec}` : "");
  const subParts = [];
  if (r.display_name && r.display_name !== smmName) subParts.push(`业务名 ${r.display_name}`);
  if (r.spec && (!smmSpec || r.spec !== smmSpec)) subParts.push(r.spec);
  subParts.push(`${r.organization} · ${r.info_category}`);
  const subLine = `<div class="product-spec">${escHtml(subParts.join(" · "))}</div>`;
  const cells = noQuote
    ? `<td class="num-cell" colspan="4"><span style="color:var(--ink-3)">暂无数据（口径说明见展开详情）</span></td>`
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
        <span class="product-name">${escHtml(primary)}</span>
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

function rowHtmlCatalog(r) {
  const q = r.quote;
  const noQuote = !q;
  const primary = r.product_name + (r.specification ? ` / ${r.specification}` : "");
  const subLine = `<div class="product-spec">${escHtml(r.category)} · ${escHtml(r.source)}</div>`;
  const cells = noQuote
    ? `<td class="num-cell" colspan="4"><span style="color:var(--ink-3)">暂无数据</span></td>`
    : `
      <td class="num-cell">${priceText(q.min_price)}</td>
      <td class="num-cell">${priceText(q.max_price)}</td>
      <td class="num-cell avg-cell">${priceText(q.average_price)}</td>
      <td class="num-cell change-cell">${changeBadge(q.change_value)}</td>`;
  return `
  <tr id="row-${escHtml(r.id)}" class="${noQuote ? "row-blank" : ""}">
    <td class="product-cell">
      <div class="product-name-row">
        <span class="product-name">${escHtml(primary)}</span>
        <span class="pname-tags"><span class="pnote">全量采集</span></span>
      </div>
      ${subLine}
    </td>
    ${cells}
    <td class="ctr">${escHtml(r.unit || (q ? q.unit : ""))}</td>
    <td class="num-cell">${q ? q.price_date : "—"}</td>
    <td class="num-cell" style="color:var(--ink-3)">${q ? fmtTs(q.collected_at) : "—"}</td>
    <td class="ctr"><span class="tag daily">日频</span></td>
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
    : `<div class="detail-item"><span class="k">当前报价</span><span class="v">暂无数据</span></div>`;
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
  if (!detail || !row) return;
  const btn = row.querySelector(".expand-btn");
  if (detail.hidden) {
    qstate.expanded.add(id);
    detail.hidden = false;
    row.classList.add("expanded");
    if (btn) btn.textContent = "▾";
  } else {
    qstate.expanded.delete(id);
    detail.hidden = true;
    row.classList.remove("expanded");
    if (btn) btn.textContent = "▸";
  }
}

function expandRow(id) {
  const detail = document.getElementById("detail-" + id);
  if (detail && detail.hidden) toggleRow(id);
}
