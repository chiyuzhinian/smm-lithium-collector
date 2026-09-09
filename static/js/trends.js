/* 价格走势：单产品区间带+指标曲线 / 同单位多产品对比（趋势分析页，无底部明细表）
   V4：图例整合图表头 · 单品种紧凑摘要行 · 日期标签无裁切 · 图表随容器 resize */
"use strict";

/* 多序列分类色：dataviz validate_palette.js 已对 #151F30 暗色表面验证通过
   （CVD 相邻 ΔE≥8.4 / 正常视觉 ≥19.3 / 对比 ≥3:1），槽位固定不循环 */
const SERIES_COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"];
const BRAND_BLUE = "#7DA7FF";
const BAND_FILL = "rgba(125, 167, 255, 0.10)";
const BAND_EDGE = "rgba(125, 167, 255, 0.30)";
const MAX_COMPARE = 5;

/* 暗色图表常量（与 app.css 令牌一致） */
const CHART = {
  axis: "#2A3A50",
  label: "#8B9BB4",
  split: "rgba(42, 58, 80, .55)",
  tipBg: "#1B283D",
  tipBorder: "#2A3A50",
  tipText: "#E6EDF7",
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
  chart: null,
  colorMap: new Map(), // 产品 id → 配色槽位（会话内稳定，移除产品不影响他者颜色）
  ro: null,            // ResizeObserver
};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  bindControls();
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
  // 图表尺寸随容器变化（视口缩放/筛选换行都会触发）
  tstate.ro = new ResizeObserver(() => {
    if (tstate.chart) tstate.chart.resize();
  });
  tstate.ro.observe(document.getElementById("chart"));

  // URL 参数：/trends?ids=a,b&from=&to=
  const params = new URLSearchParams(location.search);
  let ids = (params.get("ids") || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (!ids.length) ids = ["lce_battery"];
  for (const id of ids) {
    if (tstate.selected.length < MAX_COMPARE && tstate.products.some((p) => p.id === id)) {
      addProduct(id, true);
    }
  }
  if (params.get("from")) { tstate.range = "custom"; tstate.from = params.get("from"); tstate.to = params.get("to"); }
  syncRangeUI();
  await loadData();
  renderChart();
}

function bindControls() {
  document.getElementById("product-pick").addEventListener("input", (e) => {
    renderSuggest(e.target.value.trim());
  });
  document.getElementById("product-pick").addEventListener("focus", function () {
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
      renderChart();
    });
  });
  document.querySelectorAll("#metric-seg button").forEach((b) => {
    b.addEventListener("click", () => {
      tstate.metric = b.dataset.metric;
      document.querySelectorAll("#metric-seg button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      updateChartHead();
      renderChart();
    });
  });
  document.getElementById("from-input").addEventListener("change", async () => {
    tstate.from = document.getElementById("from-input").value || null;
    await loadData();
    renderChart();
  });
  document.getElementById("to-input").addEventListener("change", async () => {
    tstate.to = document.getElementById("to-input").value || null;
    await loadData();
    renderChart();
  });
  window.addEventListener("resize", () => tstate.chart && tstate.chart.resize());
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

function currentWindow() {
  if (tstate.range === "custom") return { from: tstate.from, to: tstate.to };
  const to = tstate.dbLatest;
  if (!to) return { from: null, to: null };
  const days = tstate.range === "7d" ? 7 : 30;
  const d = new Date(to + "T00:00:00");
  d.setDate(d.getDate() - (days - 1));
  const from = d.toISOString().slice(0, 10);
  return { from: from < (tstate.historyFrom || from) ? (tstate.historyFrom || from) : from, to };
}

/* ── 产品选择 ── */

function renderSuggest(q) {
  const box = document.getElementById("product-suggest");
  const norm = q.toLowerCase();
  let hits = tstate.products.filter((p) =>
    !norm || (p.searchable || "").toLowerCase().includes(norm));
  hits = hits.slice(0, 10);
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
        await loadData();
        renderChart();
      });
    });
  }
  box.classList.add("open");
}

function addProduct(id, silent) {
  const p = tstate.products.find((x) => x.id === id);
  if (!p || tstate.selected.includes(id)) return;
  if (tstate.selected.length >= MAX_COMPARE) {
    if (!silent) notify(`最多同时对比 ${MAX_COMPARE} 个产品`, "error");
    return;
  }
  const first = tstate.products.find((x) => x.id === tstate.selected[0]);
  if (first && first.unit !== p.unit) {
    if (!silent) notify(`单位不同（${first.unit} / ${p.unit}），不能在同一价格轴对比`, "error");
    return;
  }
  tstate.selected.push(id);
  renderChips();
}

function removeProduct(id) {
  tstate.selected = tstate.selected.filter((x) => x !== id);
  tstate.colorMap.delete(id);  // 释放槽位，保证无撞色
  renderChips();
}

/* 配色槽位：会话内稳定（颜色跟随产品，不随选择顺序/移除重排） */
function colorSlot(id) {
  if (tstate.colorMap.has(id)) return tstate.colorMap.get(id);
  for (let i = 0; i < SERIES_COLORS.length; i++) {
    if (![...tstate.colorMap.values()].includes(i)) {
      tstate.colorMap.set(id, i);
      return i;
    }
  }
  return 0;
}

function chipColor(id) {
  return tstate.selected.length === 1 ? BRAND_BLUE : SERIES_COLORS[colorSlot(id)];
}

/* 图表头：指标/单位/时间范围 + 已选产品图例（含区间带标注） */
function updateChartHead() {
  const { from, to } = currentWindow();
  const first = tstate.products.find((p) => p.id === tstate.selected[0]);
  const unit = first ? first.unit : (tstate.series[0] && tstate.series[0].unit) || "";
  const metricNames = { avg: "日均价", min: "最低价", max: "最高价" };
  const label = tstate.selected.length === 1 ? metricNames[tstate.metric] : "多产品对比";
  document.getElementById("chart-metric-label").textContent = label;
  document.getElementById("chart-range-label").textContent =
    `${unit ? unit + " · " : ""}${from && to ? from + " ~ " + to : "—"}`;
}

/* 单品种紧凑摘要：期末日均价 · 区间首末变化（带实际日期，非源字段当日涨跌） · 有效报价点数 */
function renderSummary() {
  const el = document.getElementById("chart-summary");
  if (tstate.selected.length !== 1) { el.innerHTML = ""; return; }
  const s = tstate.series.find((x) => x.id === tstate.selected[0]);
  if (!s || !s.points || !s.points.length) { el.innerHTML = ""; return; }
  const pts = s.points.filter((p) => num(p.average_price) !== null);
  const unit = s.unit || "";
  const last = pts[pts.length - 1];
  const first = pts[0];
  const lastText = `<b>${priceText(last.average_price)}</b> ${escHtml(unit)}（${escHtml(last.price_date)}）`;
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
    `<span>期末日均价 ${lastText}</span>` +
    `<span class="status-sep">·</span>` +
    `<span>区间变化 <b class="${chgCls}">${chgText}</b></span>` +
    `<span class="status-sep">·</span>` +
    `<span>有效报价点 <b>${pts.length}</b></span>`;
}

function renderChips() {
  const row = document.getElementById("chips-row");
  const chips = tstate.selected.map((id) => {
    const p = tstate.products.find((x) => x.id === id);
    if (!p) return "";
    const name = p.spec ? `${p.display_name}（${p.spec}）` : p.display_name;
    const multi = tstate.selected.length > 1;
    return `<span class="legend-chip">
      <span class="legend-dot" style="background:${chipColor(id)}"></span>
      <span class="chip-name" title="${escHtml(name)}">${escHtml(name)}</span>
      ${multi ? `<button class="legend-remove" data-id="${escHtml(id)}" aria-label="移除 ${escHtml(name)}">×</button>` : ""}
    </span>`;
  }).join("");
  const band = tstate.selected.length === 1
    ? `<span class="legend-band"><span class="band-swatch"></span>最低—最高价</span>` : "";
  row.innerHTML = chips + band;
  row.querySelectorAll(".legend-remove").forEach((btn) => {
    btn.addEventListener("click", async () => {
      removeProduct(btn.dataset.id);
      await loadData();
      renderChart();
    });
  });
  updateChartHead();
  renderSummary();
}

/* ── 数据加载 ── */

async function loadData() {
  if (!tstate.selected.length) return;
  const { from, to } = currentWindow();
  const params = new URLSearchParams();
  params.set("ids", tstate.selected.join(","));
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  try {
    const data = await API.get("/api/portal/history?" + params.toString(), 10000);
    tstate.series = data.series || [];
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

/* X 轴日期标签：同年 MM-DD，跨年 YYYY-MM-DD（Tooltip 恒为完整日期） */
function axisDateFormatter() {
  const { from, to } = currentWindow();
  const crossYear = from && to && from.slice(0, 4) !== to.slice(0, 4);
  return (v) => (crossYear ? v : String(v).slice(5));
}

function renderChart() {
  const el = document.getElementById("chart");
  if (!tstate.chart) tstate.chart = echarts.init(el);
  const single = tstate.selected.length === 1;
  const s = tstate.series.find((x) => x.id === tstate.selected[0]);
  updateChartHead();
  renderSummary();
  if (!s || !s.points || !s.points.length) {
    tstate.chart.clear();
    tstate.chart.setOption({
      title: { text: "暂无数据", left: "center", top: "middle",
               textStyle: { color: CHART.label, fontSize: 13, fontWeight: 400 } },
    });
    return;
  }

  const option = single ? singleOption(s) : multiOption();
  tstate.chart.setOption(option, true);
}

function axisCommon() {
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
      extraCssText: "box-shadow: 0 8px 24px rgba(4,8,16,.55); border-radius: 8px;",
      axisPointer: {
        type: "cross",
        lineStyle: { color: "rgba(139, 155, 180, .5)" },
        crossStyle: { color: "rgba(139, 155, 180, .5)" },
        label: { backgroundColor: CHART.tipBorder, color: CHART.tipText },
      },
    },
    xAxis: {
      type: "category",
      boundaryGap: ["5%", "5%"],   // 首尾标签整体内移，不伸出绘图区（防止右端裁切）
      data: [],                    // 各序列覆盖日期并集（统一时间轴）
      axisLine: { lineStyle: { color: CHART.axis } },
      axisTick: { show: false },
      axisLabel: { color: CHART.label, fontSize: 11 },
    },
    yAxis: {
      type: "value", scale: true,
      splitLine: { lineStyle: { color: CHART.split } },
      axisLine: { show: false },
      axisLabel: { color: CHART.label, fontSize: 11, fontFamily: "monospace" },
    },
  };
}

/* 单产品：最低-最高区间带 + 指标曲线（默认日均价） */
function singleOption(s) {
  const metric = tstate.metric;
  const dates = s.points.map((p) => p.price_date);
  const base = axisCommon();
  base.xAxis.data = dates;
  base.xAxis.axisLabel.formatter = axisDateFormatter();
  const bandMin = [], bandDiff = [], metricLine = [];
  for (const p of s.points) {
    const lo = num(p.min_price), hi = num(p.max_price), mv = num(p[metric === "avg" ? "average_price" : metric + "_price"]);
    bandMin.push(lo ?? null);
    bandDiff.push(lo !== null && hi !== null ? hi - lo : null);
    metricLine.push({ value: mv, point: p });
  }
  const metricNames = { avg: "日均价", min: "最低价", max: "最高价" };
  base.tooltip.formatter = (items) => {
    if (!items || !items.length) return "";
    const idx = items[0].dataIndex;
    const p = s.points[idx];
    const chg = changeText(p.change_value);
    const row = (k, v) => `<div class="tip-row"><span class="tip-k">${k}</span><span class="tip-v">${v}</span></div>`;
    return `<div class="chart-tip">
      <div class="tip-title">${escHtml(s.display_name)} ${escHtml(s.spec || "")}</div>
      ${row("报价日期", escHtml(p.price_date))}
      ${row("最低价", priceText(p.min_price) + " " + escHtml(p.unit))}
      ${row("最高价", priceText(p.max_price) + " " + escHtml(p.unit))}
      ${row("日均价", priceText(p.average_price) + " " + escHtml(p.unit))}
      ${row("涨跌", escHtml(chg.text))}
      ${s.frequency === "weekly" ? `<div class="tip-note">周报价（按实际发布点）</div>` : ""}
    </div>`;
  };
  base.series = [
    { name: "最低价", type: "line", data: bandMin, stack: "band",
      lineStyle: { color: BAND_EDGE, width: 1 }, symbol: "none", silent: true,
      z: 2, emphasis: { disabled: true } },
    { name: "区间带（最低—最高价）", type: "line", data: bandDiff, stack: "band",
      lineStyle: { opacity: 0 }, symbol: "none", silent: true,
      areaStyle: { color: BAND_FILL }, z: 1, emphasis: { disabled: true } },
    { name: metricNames[metric], type: "line", data: metricLine,
      lineStyle: { color: BRAND_BLUE, width: 2 },
      itemStyle: { color: BRAND_BLUE },
      symbol: "circle", symbolSize: 5,
      z: 3 },
  ];
  return base;
}

/* 多产品对比：同单位、统一日期窗口、稳定配色（颜色跟随产品）；不计算综合均价 */
function multiOption() {
  const allDates = new Set();
  for (const s of tstate.series) s.points.forEach((p) => allDates.add(p.price_date));
  const dates = [...allDates].sort();
  const base = axisCommon();
  base.xAxis.data = dates;
  base.xAxis.axisLabel.formatter = axisDateFormatter();
  base.tooltip.formatter = (items) => {
    if (!items || !items.length) return "";
    const d = items[0].name;
    const lines = items.map((it) => {
      const p = it.data.point;
      return `<div class="tip-row">
        <span class="tip-k" style="color:${it.color}">● ${escHtml(it.seriesName)}</span>
        <span class="tip-v">${priceText(p[tstate.metric === "avg" ? "average_price" : tstate.metric + "_price"])} ${escHtml(p.unit)}</span></div>`;
    }).join("");
    return `<div class="chart-tip">
      <div class="tip-title">${escHtml(d)}</div>${lines}
      <div class="tip-note" style="color:var(--ink-3)">同一坐标轴 · 同单位（${escHtml(tstate.series[0].unit)}）</div></div>`;
  };
  base.series = tstate.series.map((s) => {
    const color = SERIES_COLORS[colorSlot(s.id)];
    const data = dates.map((d) => {
      const p = s.points.find((x) => x.price_date === d);
      if (!p) return null;   // 该品种当日缺报 → 断点，不补 0、不插值
      return { value: num(p[tstate.metric === "avg" ? "average_price" : tstate.metric + "_price"]), point: p };
    });
    return {
      name: s.display_name + (s.spec ? `（${s.spec}）` : ""),
      type: "line", data, connectNulls: false,
      lineStyle: { color, width: 2 }, itemStyle: { color },
      symbol: "circle", symbolSize: 5,
    };
  });
  return base;
}
