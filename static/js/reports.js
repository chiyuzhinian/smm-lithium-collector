/* 数据与报表：①全部分类数据（全量入口） ②月度业务报表（预览 + 下载） */
"use strict";

const rstate = {
  tab: "dataset",
  ds: { cat: "", prod: "", from: "", to: "", q: "", page: 1, pageSize: 100, total: 0, cats: [] },
  monthly: null,
};

const COMP_TAG = {
  complete: '<span class="tag daily">完整</span>',
  incomplete: '<span class="tag merged">不完整</span>',
  in_progress: '<span class="tag weekly">月份未结束</span>',
  unavailable: '<span class="tag merged">暂无独立报价</span>',
  manual: '<span class="tag org">人工填写</span>',
};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  bindTabs();
  bindDataset();
  bindMonthly();
  await loadCategories();
  await searchDataset();
}

function bindTabs() {
  document.querySelectorAll("#tab-seg button").forEach((b) => {
    b.addEventListener("click", () => {
      rstate.tab = b.dataset.tab;
      document.querySelectorAll("#tab-seg button").forEach((x) => x.classList.toggle("active", x === b));
      document.getElementById("tab-dataset").hidden = rstate.tab !== "dataset";
      document.getElementById("tab-monthly").hidden = rstate.tab !== "monthly";
      if (rstate.tab === "monthly" && !rstate.monthly) loadMonthly();
    });
  });
}

/* ══════════ ① 全部分类数据 ══════════ */

function bindDataset() {
  document.getElementById("ds-cat").addEventListener("change", async () => {
    rstate.ds.cat = document.getElementById("ds-cat").value;
    await loadProducts();
    rstate.ds.page = 1;
    await searchDataset();
  });
  document.getElementById("ds-search").addEventListener("click", async () => {
    rstate.ds.q = document.getElementById("ds-q").value.trim();
    rstate.ds.from = document.getElementById("ds-from").value;
    rstate.ds.to = document.getElementById("ds-to").value;
    rstate.ds.prod = document.getElementById("ds-prod").value;
    rstate.ds.page = 1;
    await searchDataset();
  });
  document.getElementById("ds-reset").addEventListener("click", () => {
    rstate.ds = { ...rstate.ds, cat: "", prod: "", from: "", to: "", q: "", page: 1 };
    document.getElementById("ds-cat").value = "";
    document.getElementById("ds-prod").value = "";
    document.getElementById("ds-from").value = "";
    document.getElementById("ds-to").value = "";
    document.getElementById("ds-q").value = "";
    loadProducts().then(searchDataset);
  });
  document.getElementById("ds-prev").addEventListener("click", () => { rstate.ds.page--; searchDataset(); });
  document.getElementById("ds-next").addEventListener("click", () => { rstate.ds.page++; searchDataset(); });
}

async function loadCategories() {
  try {
    const data = await API.get("/api/portal/dataset/categories", 60000);
    rstate.ds.cats = data.categories || [];
    const sel = document.getElementById("ds-cat");
    for (const c of rstate.ds.cats) {
      const op = document.createElement("option");
      op.value = c.category;
      op.textContent = `${c.category}（${c.row_count} 行 · ${c.max_date}）`;
      sel.appendChild(op);
    }
  } catch (err) {
    notify("分类加载失败：" + err.message, "error");
  }
}

async function loadProducts() {
  const sel = document.getElementById("ds-prod");
  sel.innerHTML = '<option value="">全部产品</option>';
  if (!rstate.ds.cat) return;
  try {
    const data = await API.get("/api/portal/dataset/products?category=" + encodeURIComponent(rstate.ds.cat), 30000);
    for (const p of data.products || []) {
      const op = document.createElement("option");
      op.value = p.product_name;
      op.textContent = p.specification
        ? `${p.product_name} / ${p.specification}（${p.unit}）` : `${p.product_name}（${p.unit}）`;
      sel.appendChild(op);
    }
  } catch (err) { /* 忽略 */ }
}

async function searchDataset() {
  const scroll = document.getElementById("ds-scroll");
  scroll.innerHTML = loadingHtml("正在加载全部分类数据…");
  const d = rstate.ds;
  const params = new URLSearchParams();
  if (d.cat) params.set("category", d.cat);
  if (d.prod) params.set("product", d.prod);
  if (d.from) params.set("from", d.from);
  if (d.to) params.set("to", d.to);
  if (d.q) params.set("q", d.q);
  params.set("page", d.page);
  params.set("page_size", d.pageSize);
  try {
    const data = await API.get("/api/portal/dataset/quotes?" + params.toString(), 10000);
    d.total = data.total;
    renderDataset(data);
    const pages = Math.max(1, Math.ceil(data.total / d.pageSize));
    document.getElementById("ds-count").innerHTML = `共 <b>${data.total.toLocaleString()}</b> 行`;
    document.getElementById("ds-page-info").textContent = `第 ${d.page} / ${pages} 页 · 每页 ${d.pageSize} 行`;
    document.getElementById("ds-prev").disabled = d.page <= 1;
    document.getElementById("ds-next").disabled = d.page >= pages;
  } catch (err) {
    scroll.innerHTML = emptyHtml("⚠️", "全量数据加载失败：" + err.message);
  }
}

function renderDataset(data) {
  const scroll = document.getElementById("ds-scroll");
  if (!data.rows.length) {
    scroll.innerHTML = emptyHtml("📭", "没有符合条件的记录");
    return;
  }
  let html = `<table class="data-table">
    <thead><tr>
      <th>分类</th><th>产品 / 规格</th>
      <th class="num-cell">最低价</th><th class="num-cell">最高价</th>
      <th class="num-cell">日均价</th><th class="num-cell">涨跌</th>
      <th class="ctr">单位</th><th class="num-cell">报价日期</th><th class="num-cell">采集更新</th>
    </tr></thead><tbody>`;
  for (const r of data.rows) {
    const chg = changeText(r.change_value);
    const specLine = r.specification
      ? `<div class="product-spec">${escHtml(r.specification)}</div>` : "";
    html += `<tr>
      <td class="ds-cat">${escHtml(r.category)}</td>
      <td class="product-cell">
        <div class="product-name-row">
          <span class="product-name">${escHtml(r.product_name)}</span>
        </div>
        ${specLine}
      </td>
      <td class="num-cell">${priceText(r.min_price)}</td>
      <td class="num-cell">${priceText(r.max_price)}</td>
      <td class="num-cell avg-cell">${priceText(r.average_price)}</td>
      <td class="num-cell change-cell ${chg.cls}">${escHtml(chg.text)}</td>
      <td class="ctr">${escHtml(r.unit)}</td>
      <td class="num-cell">${r.price_date}</td>
      <td class="num-cell" style="color:var(--ink-3)">${fmtTs(r.collected_at)}</td>
    </tr>`;
  }
  html += "</tbody></table>";
  scroll.innerHTML = html;
}

/* ══════════ ② 月度业务报表 ══════════ */

function bindMonthly() {
  const inp = document.getElementById("month-input");
  // 默认 = 上一个自然月（最近一个已结束月份）
  const now = new Date();
  let m = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  const def = `${m.getFullYear()}-${String(m.getMonth() + 1).padStart(2, "0")}`;
  inp.value = def;
  inp.min = "2026-07";
  inp.max = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  document.getElementById("month-load").addEventListener("click", () => loadMonthly());
  inp.addEventListener("change", () => loadMonthly());
  document.getElementById("month-download").addEventListener("click", (e) => {
    const month = inp.value;
    if (!month) return;
    e.preventDefault();
    window.open(`/api/portal/monthly/download?month=${encodeURIComponent(month)}`, "_blank");
  });
}

/* 月报预览五态：未预览（紧凑提示）/ 加载 / 成功 / 无数据 / 失败（原因 + 重试） */
function monthState(html) {
  const scroll = document.getElementById("month-scroll");
  const status = document.getElementById("month-status");
  status.hidden = true;
  document.getElementById("month-count").innerHTML = "—";
  scroll.innerHTML = `<div class="table-scroll">${html}</div>`;
}

function monthErrorState(msg, retry) {
  const btn = `<div><button class="btn sm retry-btn" id="month-retry">重试</button></div>`;
  monthState(`<div class="quality-state">⚠️ 月报加载失败：${escHtml(msg)}${retry !== false ? btn : ""}</div>`);
  const b = document.getElementById("month-retry");
  if (b) b.addEventListener("click", () => loadMonthly());
}

function monthLoadingState() {
  monthState(`<div class="loading"><span class="spinner"></span>正在计算月均价与环比…</div>`);
}

async function loadMonthly() {
  const month = document.getElementById("month-input").value;
  if (!month) {
    monthState(`<div class="quality-state">选择月份后预览。月份默认取上一个自然月。</div>`);
    return;
  }
  monthLoadingState();
  try {
    const data = await API.get("/api/portal/monthly?month=" + encodeURIComponent(month), 20000);
    if (!data || !data.rows || !data.rows.length) {
      monthState(`<div class="quality-state">该月份暂无数据（可能尚未采集或未生成月报）。</div>`);
      return;
    }
    rstate.monthly = data;
    renderMonthly(data);
    renderMethod(data);
  } catch (err) {
    monthErrorState(err.message || "请求失败");
  }
}

function renderMonthly(data) {
  const s = data.summary;
  const status = document.getElementById("month-status");
  status.hidden = false;
  status.innerHTML = `
    <span><span class="status-dot${s.month_ended ? "" : " stale"}"></span>${monthLabel(s.month)}（${s.month_start} ~ ${s.month_end}）${s.month_ended ? "" : " · 月份未结束，为截至当前的期间均价"}</span>
    <span class="sep"></span><span>月均价填充 <b>${s.filled}</b> / ${s.total_rows} 行</span>
    <span class="sep"></span><span>环比计算 <b>${s.mom_computed}</b> 行 · 留空并说明 <b>${s.mom_blocked}</b> 行</span>
    <span class="sep"></span><span>日频采集日 <b class="num">${s.calendar_daily_days}/${s.calendar_daily_weekdays}</b> · 周频报价 <b class="num">${s.calendar_weekly_points}/${s.calendar_weekly_fridays}</b></span>`;
  document.getElementById("month-count").innerHTML =
    `共 <b>${s.total_rows}</b> 行 · 保留非 SMM 人工填写行`;

  const rows = data.rows;
  let html = `<table class="data-table month-grid-table">
    <thead><tr>
      <th class="ctr">组织</th><th>详细内容</th>
      <th class="num-cell">月均价</th><th class="ctr">单位</th>
      <th class="num-cell">环比</th>
      <th class="num-cell">报价点</th><th class="num-cell">覆盖范围</th>
      <th>完整性</th><th>说明</th>
    </tr></thead><tbody>`;
  // 组织行合并（rowspan）
  const orgRows = new Map();
  for (const r of rows) orgRows.set(r.organization, (orgRows.get(r.organization) || 0) + 1);
  const orgDone = new Set();
  for (const r of rows) {
    const orgCell = orgDone.has(r.organization)
      ? ""
      : `<td class="org-cell" rowspan="${orgRows.get(r.organization)}">${escHtml(r.organization)}</td>`;
    orgDone.add(r.organization);
    const mom = r.mom_pct !== null ? momBadge(r.mom_pct) : '<span style="color:var(--ink-4)">—</span>';
    const detail = r.template_detail || `${r.display_name}${r.spec ? "（" + r.spec + "）" : ""}`;
    html += `<tr class="${r.monthly_avg === null ? "row-blank" : ""}">
      ${orgCell}
      <td style="max-width:360px">
        <div style="font-weight:600;color:var(--ink)">${escHtml(detail)}</div>
        <div style="font-size:11.5px;color:var(--ink-4);margin-top:2px">${escHtml(r.info_category)} · ${escHtml(r.chemistry)} · 来源 ${escHtml(r.source)}</div>
      </td>
      <td class="num-cell avg-cell">${r.monthly_avg !== null ? escHtml(groupPrice(r.monthly_avg)) : "—"}</td>
      <td class="ctr">${escHtml(r.unit)}</td>
      <td class="num-cell mom-cell">${mom}</td>
      <td class="num-cell">${r.n_points || "—"}</td>
      <td class="num-cell" style="color:var(--ink-3)">${r.date_from ? `${r.date_from} ~ ${r.date_to}` : "—"}</td>
      <td class="ctr">${COMP_TAG[r.completeness] || escHtml(r.completeness)}</td>
      <td style="max-width:280px;font-size:12px;color:var(--ink-3)">
        ${escHtml(r.mom_reason || r.reason || "")}</td>
    </tr>`;
  }
  html += "</tbody></table>";
  document.getElementById("month-scroll").innerHTML =
    `<div class="table-scroll">${html}</div>`;
}

function momBadge(pct) {
  const n = Number(pct);
  const cls = n > 0 ? "up" : n < 0 ? "down" : "flat";
  const text = n > 0 ? `+${pct}%` : `${pct}%`;
  return `<span class="change-badge ${cls}">${text}</span>`;
}

function renderMethod(data) {
  const s = data.summary;
  const unresolved = data.rows.filter((r) => r.mapping_status === "unavailable_merged");
  const manual = data.rows.filter((r) => r.mapping_status === "manual");
  const lines = [
    `<b>月均价口径：</b>${escHtml(s.method_note || "")}`,
    `<b>完整性阈值：</b>日频 ≥80%（报价日/采集日历，采集日历自身需覆盖当月工作日 ≥80%）；周频 ≥75%（当月发布报价点/周五数）。不看月初月末是否有数据，按实际缺口判定。`,
    `<b>环比规则：</b>（本月月均价−上月月均价）÷上月月均价×100%。仅相邻两月均完整且上月月均价非零时计算；不完整则留空并在行内与备注说明原因。`,
    `<b>7 月不完整说明：</b>采集自 2026-07-20 开始，7 月采集日 8/23 个工作日，按约定 8 月环比留空。`,
    `<b>日均价与月均价区别：</b>日均价 = 某一报价日期的 SMM average_price 源字段；月均价 = 当月有效报价 average_price 的算术平均（周频按当月实际发布报价点计，重复采集不增权重）。`,
    `<b>无独立报价（${unresolved.length} 行）：</b>` +
      unresolved.map((r) => `行${r.template_row} ${escHtml(r.display_name)}（${escHtml(r.spec.slice(0, 40))}…）：${escHtml(r.reason)}`).join("；"),
    `<b>人工填写行（${manual.length} 行，非 SMM）：</b>` +
      manual.map((r) => `行${r.template_row} ${escHtml(r.display_name)}（${escHtml(r.source)}）`).join("；"),
  ];
  document.getElementById("method-body").innerHTML = lines.map((l) => `<div>${l}</div>`).join("");
}
