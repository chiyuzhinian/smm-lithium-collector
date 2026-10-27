/* 价格走势：每个产品独立 Chart Card（多选后不混在同一坐标系）
   V5：点选多产品不限数量；跨单位自动双 Y 轴（>2 种单位提示不可比）；
   图例带单位；区间内无点标「暂无历史价格」；无选择显示引导。
   V6：产品下拉全量（无 10 条上限）；调色板 10 色外黄金角扩展（>10 不撞色）。
   V7：单 chart → N 个独立 ECharts 实例 + 独立 Y 轴（解决不同产品价格量级压缩问题）。 */
"use strict";

/* 多序列分类色：前 10 槽位固定（浅色表面友好，色相区分大），槽位随产品稳定不循环；
   第 11+ 用黄金角 HSL 顺序生成（colorAt），保证 10+ 产品不撞色 */
const SERIES_COLORS = [
  "#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed",
  "#0891b2", "#db2777", "#65a30d", "#ea580c", "#0d9488",
];
function colorAt(i) {
  if (i < SERIES_COLORS.length) return SERIES_COLORS[i];
  const h = Math.round((i - SERIES_COLORS.length) * 137.508 + 210) % 360;
  return `hsl(${h} 65% 45%)`;
}
const BRAND_BLUE = "#2563eb";
const BAND_FILL = "rgba(37, 99, 235, 0.10)";
const BAND_EDGE = "rgba(37, 99, 35, 0.30)";

/* 浅色图表常量（与 app.css 浅色令牌一致） */
const CHART = {
  axis: "#CBD5E1",
  label: "#64748B",
  split: "rgba(226, 232, 240, .9)",
  tipBg: "#FFFFFF",
  tipBorder: "#E2E8F0",
  tipText: "#1E293B",
};

const tstate = {
  products: [],
  selected: [],       // 有序 ids
  range: "7d",
  from: null,
  to: null,
  metric: "avg",
  dbLatest: null,
  historyFrom: null,
  series: [],         // 服务端返回
  charts: new Map(),  // id -> ECharts instance
  chartEls: new Map(),// id -> DOM element
  ro: null,            // ResizeObserver
  colorMap: new Map(), // 产品 id → 配色槽位（仅用于 chip 颜色，与 Y 轴无关）
};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  bindControls();
  // 指标默认态由 JS 唯一控制（默认日均价，不依赖 HTML class）
  document.querySelectorAll("#metric-seg button").forEach((b) =>
    b.classList.toggle("active", b.dataset.metric === tstate.metric));
  try {
    const data = await API.get("/api/portal/products", 60000);
    tstate.products = data.products.filter((p) => p.source === "SMM");
    tstate.dbLatest = data.meta.db_latest_date;
    tstate.historyFrom = data.meta.history_from;
    const fromInput = document.getElementById("from-input");
    const toInput = document.getElementById("to-input");
    if (tstate.historyFrom) fromInput.min = tstate.historyFrom;
    if (tstate.dbLatest) { fromInput.max = tstate.dbLatest; toInput.max = tstate.dbLatest; }
  } catch (err) {
    notify("产品映射加载失败：" + err.message, "error");
    return;
  }
  // 容器尺寸监听（多 chart 用统一 observer）
  tstate.ro = new ResizeObserver(() => {
    for (const inst of tstate.charts.values()) inst.resize();
  });
  tstate.ro.observe(document.getElementById("chart-grid"));

  // URL 参数：/trends?ids=a,b&from=&to=；无参数 = 空选择（引导用户点选）
  const params = new URLSearchParams(location.search);
  const urlIds = (params.get("ids") || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (urlIds.length) {
    // URL 显式导航（如每日报价「查看走势」）优先，且不触发偏好保存
    for (const id of urlIds) {
      if (tstate.products.some((p) => p.id === id)) addProduct(id, true);
    }
  } else {
    // 恢复已保存的产品选择（先目录后偏好；无记录=空选择引导；失效 id 静默丢弃）
    const saved = await Prefs.load("price_trends");
    if (Array.isArray(saved)) {
      saved.filter((id) => tstate.products.some((p) => p.id === id))
           .forEach((id) => addProduct(id, true));
    }
  }
  if (params.get("from")) { tstate.range = "custom"; tstate.from = params.get("from"); tstate.to = params.get("to"); }
  syncRangeUI();
  await loadData();
  renderChips();   // 恢复后渲染产品图例标签（与选中状态/图表一致；series 已就绪再渲染避免误标「暂无报价」）
  renderCharts();
}

function bindControls() {
  document.getElementById("product-pick").addEventListener("input", (e) => {
    renderSuggest(e.target.value.trim());
  });
  document.getElementById("product-pick").addEventListener("focus", function () {
    renderSuggest(this.value.trim());
  });
  document.getElementById("product-pick").addEventListener("click", function () {
    renderSuggest(this.value.trim());
  });
  document.addEventListener("click", (e) => {
    const s = document.getElementById("product-suggest");
    if (s && !e.target.closest(".search-wrap")) s.classList.remove("open");
  });
  document.querySelectorAll("#range-seg button").forEach((b) => {
    b.addEventListener("click", async () => {
      tstate.range = b.dataset.range;
      syncRangeUI();
      await loadData();
      renderCharts();
    });
  });
  document.querySelectorAll("#metric-seg button").forEach((b) => {
    b.addEventListener("click", () => {
      tstate.metric = b.dataset.metric;
      document.querySelectorAll("#metric-seg button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      updateChartHead();
      renderCharts();
    });
  });
  document.getElementById("from-input").addEventListener("change", async () => {
    tstate.from = document.getElementById("from-input").value || null;
    await loadData();
    renderCharts();
  });
  document.getElementById("to-input").addEventListener("change", async () => {
    tstate.to = document.getElementById("to-input").value || null;
    await loadData();
    renderCharts();
  });
  window.addEventListener("resize", () => {
    for (const inst of tstate.charts.values()) inst.resize();
  });
}

function syncRangeUI() {
  document.querySelectorAll("#range-seg button").forEach((b) =>
    b.classList.toggle("active", b.dataset.range === tstate.range));
  const custom = document.getElementById("custom-range");
  custom.hidden = tstate.range !== "custom";
  if (tstate.range === "custom") {
    if (tstate.from) document.getElementById("from-input").value = tstate.from;
    if (tstate.to) document.getElementById("to-input").value = tstate.to;
  }
}

/* 取窗口径（2026-10-19 确认）：
   - custom：用户所选区间，后端 from/to 已过滤真实点
   - 7d/1m：每产品各自截至最新有效报价日、向前取最近 7/30 个「有数据点的报价日」
   （按有数据点计数，不是自然日；不足 N 个显示实际有的；绝不补点/插值） */
function currentWindow() {
  if (tstate.range === "custom") return { mode: "custom", from: tstate.from, to: tstate.to };
  return { mode: "lastN", n: tstate.range === "7d" ? 7 : 30 };
}

/* 对每个产品应用窗口（多产品各产品独立取窗，不跨产品对齐） */
function applyWindow(seriesList) {
  const win = currentWindow();
  if (win.mode !== "lastN") return seriesList;   // custom：后端已过滤
  return seriesList.map((s) => {
    const pts = s.points || [];
    const sliced = pts.length > win.n ? pts.slice(pts.length - win.n) : pts;
    return Object.assign({}, s, { points: sliced });
  });
}

/* ── 产品选择 ── */

function renderSuggest(q) {
  const box = document.getElementById("product-suggest");
  const norm = q.toLowerCase();
  const hits = tstate.products.filter((p) =>
    !norm || (p.searchable || "").toLowerCase().includes(norm));
  if (!hits.length) {
    box.innerHTML = `<div class="suggest-empty">没有匹配的产品</div>`;
  } else {
    box.innerHTML = hits.map((p) => {
      const picked = tstate.selected.includes(p.id);
      return `<button type="button" class="suggest-item" data-id="${escHtml(p.id)}">
        <div class="suggest-name">${picked ? "✓ " : ""}${escHtml(p.display_name)}
          ${p.spec ? `<span style="font-weight:400;color:var(--ink-3)"> · ${escHtml(p.spec.slice(0, 32))}${p.spec.length > 32 ? "…" : ""}</span>` : ""}
          <span style="float:right;color:var(--ink-3);font-size:11px">${escHtml(p.unit)}</span></div>
        <div class="suggest-spec">${escHtml(p.organization)} · ${escHtml(p.info_category)}${p.frequency === "weekly" ? " · 周报价" : ""}</div>
      </button>`;
    }).join("");
    box.querySelectorAll(".suggest-item").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.dataset.id;
        if (tstate.selected.includes(id)) removeProduct(id);
        else addProduct(id);
        box.classList.remove("open");
        document.getElementById("product-pick").value = "";
        saveSelected();
        await loadData();
        renderCharts();
      });
    });
  }
  box.classList.add("open");
}

function addProduct(id, silent) {
  const p = tstate.products.find((x) => x.id === id);
  if (!p || tstate.selected.includes(id)) return;
  tstate.selected.push(id);
  if (!silent) renderChips();
}

function removeProduct(id) {
  tstate.selected = tstate.selected.filter((x) => x !== id);
  tstate.colorMap.delete(id);
  renderChips();
}

/* 配色槽位：会话内稳定（颜色跟随产品，不随选择顺序/移除重排）；超过 10 个顺序分配扩展槽 */
function colorSlot(id) {
  if (tstate.colorMap.has(id)) return tstate.colorMap.get(id);
  for (let i = 0; i < SERIES_COLORS.length; i++) {
    if (![...tstate.colorMap.values()].includes(i)) {
      tstate.colorMap.set(id, i);
      return i;
    }
  }
  const used = [...tstate.colorMap.values()];
  const next = used.length ? Math.max(...used) + 1 : SERIES_COLORS.length;
  tstate.colorMap.set(id, next);
  return next;
}

function chipColor(id) {
  return tstate.selected.length === 1 ? BRAND_BLUE : colorAt(colorSlot(id));
}

/* 图表头：指标/单位/时间范围 + 已选产品图例 */
function updateChartHead() {
  const win = currentWindow();
  const units = [...new Set(tstate.series.map((s) => s.unit).filter(Boolean))];
  const unit = units.length === 1 ? units[0] : (units.length > 1 ? "多单位" : "");
  const metricNames = { avg: "日均价", min: "最低价", max: "最高价" };
  const label = tstate.selected.length === 1 ? metricNames[tstate.metric] : `${tstate.selected.length} 个产品独立走势`;
  let rangeText;
  if (win.mode === "lastN") {
    rangeText = `近 ${win.n} 个报价日`;
    // 单产品时补充该产品窗口内实际首末报价日期
    if (tstate.selected.length === 1 && tstate.series.length === 1) {
      const pts = tstate.series[0].points || [];
      if (pts.length) {
        rangeText += `（${pts[0].price_date} ~ ${pts[pts.length - 1].price_date}）`;
      }
    }
  } else {
    rangeText = win.from && win.to ? `${win.from} ~ ${win.to}` : "—";
  }
  document.getElementById("chart-metric-label").textContent = label;
  document.getElementById("chart-range-label").textContent =
    `${unit ? unit + " · " : ""}${rangeText}`;
}

/* 摘要：期末日均价 + 区间变化 + 无历史价格产品 + 多单位提示 */
function renderSummary() {
  const el = document.getElementById("chart-summary");
  const emptyIds = tstate.series
    .filter((s) => !s.points || !s.points.length)
    .map((s) => tstate.products.find((p) => p.id === s.id))
    .filter(Boolean);
  const emptyNote = emptyIds.length
    ? `<span class="status-sep">·</span><span>该区间暂无报价：${emptyIds.map((p) => `${escHtml(p.display_name)}（${escHtml(p.unit)}）`).join("、")}</span>` : "";
  const multiUnitNote = tstate.selected.length > 1 &&
    new Set(tstate.series.map((s) => s.unit).filter(Boolean)).size > 2
    ? `<span class="status-sep">·</span><span>所选产品单位超过 2 种，每张图独立 Y 轴仅供参考</span>` : "";
  if (tstate.selected.length === 0) {
    el.innerHTML = "";
    return;
  }
  if (tstate.selected.length === 1) {
    const s = tstate.series.find((x) => x.id === tstate.selected[0]);
    if (!s || !s.points || !s.points.length) { el.innerHTML = emptyNote + multiUnitNote; return; }
    const pts = s.points.filter((p) => num(p.average_price) !== null);
    const unit = s.unit || "";
    const last = pts[pts.length - 1];
    const first = pts[0];
    // 日均价（已确认口径）：窗口内有效报价点 average_price 求和 ÷ 有效报价点数量；
    // 缺报日期不参与分母、不补造数据点（曲线算法不变，此处仅摘要展示）
    const sum = pts.reduce((acc, p) => acc + num(p.average_price), 0);
    const avg = pts.length ? sum / pts.length : null;
    const avgText = avg === null ? "—" :
      priceText(avg.toFixed(Math.abs(avg) >= 100 ? 0 : Math.abs(avg) >= 1 ? 2 : 4));
    let chgText = "—";
    let chgCls = "sum-flat";
    if (pts.length >= 2 && first !== last) {
      const f = num(first.average_price), l = num(last.average_price);
      if (f && f !== 0) {
        const diff = l - f;
        const pct = (diff / f) * 100;
        const sign = diff > 0 ? "+" : "";
        chgText = `${sign}${groupPrice(diff.toFixed(Math.abs(diff) >= 100 ? 0 : Math.abs(diff) >= 1 ? 2 : 4))} / ${sign}${pct.toFixed(2)}%（${escHtml(first.price_date)} → ${escHtml(last.price_date)}）`;
        chgCls = diff > 0 ? "sum-up" : diff < 0 ? "sum-down" : "sum-flat";
      }
    }
    el.innerHTML =
      `<span>日均价：<b>${avgText}</b> ${escHtml(unit)}</span>` +
      `<span class="status-sep">·</span>` +
      `<span>区间变化 <b class="${chgCls}">${chgText}</b></span>` +
      `<span class="status-sep">·</span>` +
      `<span>有效报价点 <b>${pts.length}</b></span>` +
      emptyNote + multiUnitNote;
    return;
  }
  el.innerHTML = `<span>已为 <b>${tstate.selected.length}</b> 个产品生成独立走势图</span>` + emptyNote + multiUnitNote;
}

function renderChips() {
  const row = document.getElementById("chips-row");
  const chips = tstate.selected.map((id) => {
    const p = tstate.products.find((x) => x.id === id);
    if (!p) return "";
    const s = tstate.series.find((x) => x.id === id);
    const empty = s && (!s.points || !s.points.length);
    const name = p.spec ? `${p.display_name}（${p.spec}）` : p.display_name;
    return `<span class="legend-chip">
      <span class="legend-dot" style="background:${chipColor(id)}"></span>
      <span class="chip-name" title="${escHtml(name)}">${escHtml(name)}${p.unit ? `<span class="chip-unit">${escHtml(p.unit)}</span>` : ""}${empty ? `<span class="chip-empty">该区间暂无报价</span>` : ""}</span>
      <button class="legend-remove" data-id="${escHtml(id)}" aria-label="移除 ${escHtml(name)}">×</button>
    </span>`;
  }).join("");
  const band = tstate.selected.length === 1
    ? `<span class="legend-band"><span class="band-swatch"></span>最低—最高价</span>` : "";
  row.innerHTML = chips + band;
  row.querySelectorAll(".legend-remove").forEach((btn) => {
    btn.addEventListener("click", async () => {
      removeProduct(btn.dataset.id);
      saveSelected();
      await loadData();
      renderCharts();
    });
  });
  updateChartHead();
  renderSummary();
}

/* 保存产品选择（fire-and-forget；失败 toast 提示不阻塞图表；恢复流程走 silent 路径不写库） */
function saveSelected() {
  Prefs.save("price_trends", tstate.selected.slice()).catch((err) =>
    notify("产品选择保存失败：" + (err.data && err.data.error ? err.data.error : err.message), "error"));
}

/* ── 数据加载 ── */

async function loadData() {
  if (!tstate.selected.length) { tstate.series = []; return; }
  const win = currentWindow();
  const params = new URLSearchParams();
  params.set("ids", tstate.selected.join(","));
  // lastN（7d/1m）：不传 from/to，拉全历史后由 applyWindow 按「有数据点」取窗；
  // custom：传区间，后端按真实点过滤
  if (win.mode === "custom") {
    if (win.from) params.set("from", win.from);
    if (win.to) params.set("to", win.to);
  }
  try {
    const data = await API.get("/api/portal/history?" + params.toString(), 10000);
    tstate.series = applyWindow(data.series || []);
  } catch (err) {
    notify("走势数据加载失败：" + err.message, "error");
    tstate.series = [];
  }
}

/* ── 图表 ── */

function num(v) {
  const n = Number(v);
  return isFinite(n) ? n : null;
}

/* X 轴日期标签：同年 MM-DD，跨年 YYYY-MM-DD（category 轴收到日期字符串） */
function axisDateFormatter() {
  const win = currentWindow();
  let crossYear = false;
  if (win.mode === "custom" && win.from && win.to) {
    crossYear = win.from.slice(0, 4) !== win.to.slice(0, 4);
  } else {
    const all = tstate.series.flatMap((s) => (s.points || []).map((p) => p.price_date));
    if (all.length > 1) crossYear = all[0].slice(0, 4) !== all[all.length - 1].slice(0, 4);
  }
  return (v) => (crossYear ? v : String(v).slice(5));
}

/* 销毁旧 chart 实例 */
function disposeCharts() {
  for (const inst of tstate.charts.values()) {
    try { inst.dispose(); } catch (e) { void e; }
  }
  tstate.charts.clear();
  tstate.chartEls.clear();
}

/* 渲染 N 个独立 chart card */
function renderCharts() {
  disposeCharts();
  const grid = document.getElementById("chart-grid");
  grid.innerHTML = "";
  updateChartHead();
  renderSummary();

  if (!tstate.selected.length) {
    grid.innerHTML = `<div class="chart-empty-state">请搜索并添加产品（可多选、可跨单位对比）</div>`;
    return;
  }

  // 按选择顺序为每个产品创建独立 chart card
  for (const id of tstate.selected) {
    const p = tstate.products.find((x) => x.id === id);
    if (!p) continue;
    const s = tstate.series.find((x) => x.id === id);
    const card = document.createElement("div");
    card.className = "chart-card";
    card.dataset.id = id;
    const hasData = s && s.points && s.points.length;
    const headName = escHtml(p.display_name);
    const headSpec = p.spec ? `<span class="chart-card-spec">${escHtml(p.spec)}</span>` : "";
    const headUnit = p.unit ? `<span class="chart-card-unit">${escHtml(p.unit)}</span>` : "";
    const metricNames = { avg: "日均价", min: "最低价", max: "最高价" };
    const headMetric = `<span class="chart-card-metric">${escHtml(metricNames[tstate.metric] || "日均价")}</span>`;
    const empty = hasData ? "" : `<div class="chart-card-empty">该区间暂无报价</div>`;
    card.innerHTML = `
      <div class="chart-card-head">
        <span class="chart-card-title">${headName}</span>
        ${headSpec}
        ${headUnit}
        ${headMetric}
      </div>
      <div class="chart-instance" data-id="${escHtml(id)}"></div>
      ${empty}
    `;
    grid.appendChild(card);
    const el = card.querySelector(".chart-instance");
    tstate.chartEls.set(id, el);
    if (!hasData) continue;
    const inst = echarts.init(el);
    tstate.charts.set(id, inst);
    inst.setOption(buildCardOption(s, p), true);
  }
}

/* 单 chart card 配置：min-max 区间带 + 当前指标线，独立 Y 轴 scale: true。
   V8：xAxis 曾改 time 轴（min/max=所选窗口）；V9 移除 markPoint。
   V10（2026-10-19 确认口径）：xAxis 回归 category 轴，横轴只列真实报价日期
   （无数据日期不出现在轴上）；axisLabel hideOverlap+auto 抽稀修复标签重叠；
   数据为标量数组（与 category 轴 data 一一对应）。 */
function buildCardOption(s, p) {
  const metric = tstate.metric;
  const dates = s.points.map((pt) => pt.price_date);
  const bandMin = [], bandDiff = [], metricLine = [];
  for (const pt of s.points) {
    const lo = num(pt.min_price), hi = num(pt.max_price),
          mv = num(pt[metric === "avg" ? "average_price" : metric + "_price"]);
    bandMin.push(lo ?? null);
    bandDiff.push(lo !== null && hi !== null ? hi - lo : null);
    metricLine.push(mv);
  }
  const metricNames = { avg: "日均价", min: "最低价", max: "最高价" };
  return {
    grid: { left: 12, right: 24, top: 16, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      confine: true,
      backgroundColor: CHART.tipBg,
      borderColor: CHART.tipBorder,
      borderWidth: 1,
      padding: [10, 12],
      textStyle: { color: CHART.tipText, fontSize: 12 },
      extraCssText: "box-shadow: 0 8px 24px rgba(15, 23, 42, .14); border-radius: 8px;",
      axisPointer: {
        type: "cross",
        lineStyle: { color: "rgba(100, 116, 139, .5)" },
        crossStyle: { color: "rgba(100, 116, 139, .5)" },
        label: { backgroundColor: CHART.tipBorder, color: CHART.tipText },
      },
      formatter: (items) => {
        if (!items || !items.length) return "";
        const idx = items[0].dataIndex;
        const pt = s.points[idx];
        const chg = changeText(pt.change_value);
        const row = (k, v) => `<div class="tip-row"><span class="tip-k">${k}</span><span class="tip-v">${v}</span></div>`;
        return `<div class="chart-tip">
          <div class="tip-title">${escHtml(p.display_name)} ${escHtml(p.spec || "")}</div>
          ${row("报价日期", escHtml(pt.price_date))}
          ${row("最低价", priceText(pt.min_price) + " " + escHtml(pt.unit))}
          ${row("最高价", priceText(pt.max_price) + " " + escHtml(pt.unit))}
          ${row("日均价", priceText(pt.average_price) + " " + escHtml(pt.unit))}
          ${row("涨跌", escHtml(chg.text))}
          ${p.frequency === "weekly" ? `<div class="tip-note">周报价（按实际发布点）</div>` : ""}
        </div>`;
      },
    },
    xAxis: {
      type: "category",
      boundaryGap: ["3%", "3%"],
      data: dates,
      axisLine: { lineStyle: { color: CHART.axis } },
      axisTick: { show: false },
      // hideOverlap + auto 抽稀：长区间标签密集时自动隐藏部分标签防重叠（不删真实数据点）
      axisLabel: { color: CHART.label, fontSize: 11,
                   formatter: axisDateFormatter(), interval: "auto", hideOverlap: true },
    },
    yAxis: {
      type: "value", scale: true,     // 关键：每张图独立 Y 轴，按自身数据范围缩放
      splitLine: { lineStyle: { color: CHART.split } },
      axisLine: { show: false },
      axisLabel: { color: CHART.label, fontSize: 11, fontFamily: "monospace" },
    },
    series: [
      { name: "最低价", type: "line", data: bandMin, stack: "band",
        lineStyle: { color: BAND_EDGE, width: 1 }, symbol: "none", silent: true,
        z: 2, emphasis: { disabled: true } },
      { name: "区间带（最低—最高价）", type: "line", data: bandDiff, stack: "band",
        lineStyle: { opacity: 0 }, symbol: "none", silent: true,
        areaStyle: { color: BAND_FILL }, z: 1, emphasis: { disabled: true } },
      { name: metricNames[metric], type: "line", data: metricLine,
        connectNulls: false,
        lineStyle: { color: BRAND_BLUE, width: 2 },
        itemStyle: { color: BRAND_BLUE },
        symbol: "circle", symbolSize: 5,
        z: 3 },
    ],
  };
}
