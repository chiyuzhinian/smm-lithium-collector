/* 数据与报表：①价格对比（首页产品多选 × 日期范围 → /api/portal/history） ②月度业务报表（预览 + 下载）
   V6：删除历史归档与固定汇总入口；分类检索改为产品价格对比；月报月份下限由数据库历史起点驱动。 */
"use strict";

const rstate = {
  tab: "dataset",
  ds: { products: [], picked: [], from: "", to: "", series: [], dbLatest: null, historyFrom: null },
  monthly: null,
  historyFrom: null,
};

const COMP_TAG = {
  complete: '<span class="tag daily">完整</span>',
  incomplete: '<span class="tag merged">不完整</span>',
  in_progress: '<span class="tag weekly">月份未结束</span>',
  unavailable: '<span class="tag merged">暂无数据</span>',
  manual: '<span class="tag org">人工填写</span>',
};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  bindTabs();
  bindDataset();
  bindMonthly();
  // 产品列表 + 历史起点/最新日期（同一接口，与首页/走势共用，不另维护列表）
  try {
    const data = await API.get("/api/portal/products", 60000);
    rstate.ds.products = data.products.filter((p) => p.source === "SMM");
    rstate.ds.dbLatest = data.meta.db_latest_date;
    rstate.ds.historyFrom = data.meta.history_from;
    rstate.historyFrom = data.meta.history_from;
    if (data.meta.history_from) {
      document.getElementById("month-input").min = data.meta.history_from.slice(0, 7);
    }
    setupDateDefaults();
  } catch (err) {
    notify("产品列表加载失败：" + err.message, "error");
  }
  await searchDataset();   // 无选择 = 全部 40 个业务品种，页面加载即有对比表
}

function setupDateDefaults() {
  const to = rstate.ds.dbLatest;
  if (!to) return;
  const fromInput = document.getElementById("ds-from");
  const toInput = document.getElementById("ds-to");
  if (rstate.ds.historyFrom) fromInput.min = rstate.ds.historyFrom;
  if (rstate.ds.dbLatest) { fromInput.max = rstate.ds.dbLatest; toInput.max = rstate.ds.dbLatest; }
  const d = new Date(to + "T00:00:00");
  d.setDate(d.getDate() - 29);
  let from = d.toISOString().slice(0, 10);
  if (from < (rstate.ds.historyFrom || from)) from = rstate.ds.historyFrom || from;
  rstate.ds.from = from;
  rstate.ds.to = to;
  fromInput.value = from;
  toInput.value = to;
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

/* ══════════ ① 价格对比 ══════════ */

function bindDataset() {
  document.getElementById("ds-prod-pick").addEventListener("click", () => {
    const box = document.getElementById("ds-prod-suggest");
    if (box.classList.contains("open")) box.classList.remove("open");
    else renderProdSuggest(true);
  });
  document.addEventListener("click", (e) => {
    const s = document.getElementById("ds-prod-suggest");
    if (s && !e.target.closest("#ds-prod-pick") && !e.target.closest("#ds-prod-suggest")) {
      s.classList.remove("open");
    }
  });
  document.getElementById("ds-search").addEventListener("click", async () => {
    rstate.ds.from = document.getElementById("ds-from").value;
    rstate.ds.to = document.getElementById("ds-to").value;
    await searchDataset();
  });
  document.getElementById("ds-reset").addEventListener("click", async () => {
    rstate.ds.picked = [];
    renderProdChips();
    setupDateDefaults();
    await searchDataset();
  });
}

function renderProdSuggest(open) {
  const box = document.getElementById("ds-prod-suggest");
  if (!open) { box.classList.remove("open"); return; }
  const hits = rstate.ds.products;
  if (!hits.length) {
    box.innerHTML = `<div class="suggest-empty">产品列表加载中…</div>`;
  } else {
    box.innerHTML = hits.map((p) => {
      const picked = rstate.ds.picked.includes(p.id);
      return `<button type="button" class="suggest-item" data-id="${escHtml(p.id)}">
        <div class="suggest-name">${picked ? "✓ " : ""}${escHtml(p.display_name)}
          ${p.spec ? `<span style="font-weight:400;color:var(--ink-3)"> · ${escHtml(p.spec.slice(0, 32))}${p.spec.length > 32 ? "…" : ""}</span>` : ""}
          <span style="float:right;color:var(--ink-3);font-size:11px">${escHtml(p.unit)}</span></div>
        <div class="suggest-spec">SMM ${escHtml((p.series[0] || {}).product_name || "")} · ${escHtml(p.info_category)}${p.frequency === "weekly" ? " · 周报价" : ""}</div>
      </button>`;
    }).join("");
    box.querySelectorAll(".suggest-item").forEach((btn) => {
      btn.addEventListener("click", async () => {
        togglePicked(btn.dataset.id);
        box.classList.remove("open");
        await searchDataset();
      });
    });
  }
  box.classList.add("open");
}

function togglePicked(id) {
  const i = rstate.ds.picked.indexOf(id);
  if (i >= 0) rstate.ds.picked.splice(i, 1);
  else rstate.ds.picked.push(id);
  renderProdChips();
}

function renderProdChips() {
  const row = document.getElementById("ds-prod-chips");
  row.innerHTML = rstate.ds.picked.map((id) => {
    const p = rstate.ds.products.find((x) => x.id === id);
    if (!p) return "";
    const name = p.spec ? `${p.display_name}（${p.spec}）` : p.display_name;
    return `<span class="legend-chip">
      <span class="chip-name" title="${escHtml(name)}">${escHtml(name)}</span>
      <button class="legend-remove" data-id="${escHtml(id)}" aria-label="移除 ${escHtml(name)}">×</button>
    </span>`;
  }).join("");
  row.querySelectorAll(".legend-remove").forEach((btn) => {
    btn.addEventListener("click", async () => {
      togglePicked(btn.dataset.id);
      await searchDataset();
    });
  });
}

async function searchDataset() {
  const scroll = document.getElementById("ds-scroll");
  scroll.innerHTML = loadingHtml("正在加载价格对比…");
  const ids = rstate.ds.picked.length
    ? rstate.ds.picked
    : rstate.ds.products.map((p) => p.id);
  if (!ids.length) {
    scroll.innerHTML = emptyHtml("📭", "产品列表不可用");
    return;
  }
  const params = new URLSearchParams();
  params.set("ids", ids.join(","));
  if (rstate.ds.from) params.set("from", rstate.ds.from);
  if (rstate.ds.to) params.set("to", rstate.ds.to);
  try {
    const data = await API.get("/api/portal/history?" + params.toString(), 10000);
    rstate.ds.series = data.series || [];
    renderComparison(data);
  } catch (err) {
    scroll.innerHTML = emptyHtml("⚠️", "价格对比加载失败：" + err.message);
  }
}

/* 价格对比表：真实报价点逐点成行（日期倒序、产品按所选顺序）；无数据产品单独标注 */
function renderComparison(data) {
  const scroll = document.getElementById("ds-scroll");
  const series = data.series || [];
  const rows = [];
  const emptyIds = [];
  for (const s of series) {
    if (!s.points || !s.points.length) {
      emptyIds.push(s.id);
      continue;
    }
    for (const p of s.points) {
      rows.push({
        date: p.price_date, id: s.id,
        name: s.display_name, spec: s.spec || "",
        min: p.min_price, max: p.max_price, avg: p.average_price, unit: p.unit,
      });
    }
  }
  const idOrder = new Map(series.map((s, i) => [s.id, i]));
  rows.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : idOrder.get(a.id) - idOrder.get(b.id)));

  let head = "";
  if (emptyIds.length) {
    const names = emptyIds.map((id) => {
      const p = rstate.ds.products.find((x) => x.id === id);
      return p ? `${p.display_name}（${p.unit}）` : id;
    }).join("、");
    head += `<div class="quality-state">「${escHtml(names)}」在所选时间范围内暂无数据</div>`;
  }
  if (!rows.length) {
    scroll.innerHTML = head + emptyHtml("📭", "没有符合条件的记录");
    return;
  }
  const body = rows.map((r) => `
    <tr>
      <td class="num-cell">${escHtml(r.date)}</td>
      <td class="product-cell"><span class="product-name">${escHtml(r.name)}</span></td>
      <td>${escHtml(r.spec) || '<span style="color:var(--ink-4)">—</span>'}</td>
      <td class="num-cell">${priceText(r.min)}</td>
      <td class="num-cell">${priceText(r.max)}</td>
      <td class="num-cell avg-cell">${priceText(r.avg)}</td>
      <td class="ctr">${escHtml(r.unit)}</td>
    </tr>`).join("");
  scroll.innerHTML = head + `<table class="data-table">
    <thead><tr>
      <th class="num-cell">日期</th><th>产品</th><th>规格</th>
      <th class="num-cell">最低价</th><th class="num-cell">最高价</th>
      <th class="num-cell">日均价</th><th class="ctr">单位</th>
    </tr></thead>
    <tbody>${body}</tbody>
  </table>`;
  document.getElementById("ds-count").innerHTML =
    `共 <b>${rows.length}</b> 个报价点 · ${series.length} 个产品` +
    (emptyIds.length ? ` · ${emptyIds.length} 个暂无数据` : "");
}

/* ══════════ ② 月度业务报表 ══════════ */

function bindMonthly() {
  const inp = document.getElementById("month-input");
  // 默认 = 上一个自然月（最近一个已结束月份）
  const now = new Date();
  let m = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  const def = `${m.getFullYear()}-${String(m.getMonth() + 1).padStart(2, "0")}`;
  inp.value = def;
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
    rstate.historyFrom
      ? `<b>历史起点：</b>采集自 ${escHtml(rstate.historyFrom)} 开始，起始月份数据不完整时对应环比留空。`
      : "",
    `<b>日均价与月均价区别：</b>日均价 = 某一报价日期的 SMM average_price 源字段；月均价 = 当月有效报价 average_price 的算术平均（周频按当月实际发布报价点计，重复采集不增权重）。`,
    `<b>无独立报价（${unresolved.length} 行）：</b>` +
      unresolved.map((r) => `行${r.template_row} ${escHtml(r.display_name)}（${escHtml(r.spec.slice(0, 40))}…）：${escHtml(r.reason)}`).join("；"),
    `<b>人工填写行（${manual.length} 行，非 SMM）：</b>` +
      manual.map((r) => `行${r.template_row} ${escHtml(r.display_name)}（${escHtml(r.source)}）`).join("；"),
  ].filter(Boolean);
  document.getElementById("method-body").innerHTML = lines.map((l) => `<div>${l}</div>`).join("");
}
