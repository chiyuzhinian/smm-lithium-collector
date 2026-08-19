/* 共享图表渲染：SVG sparkline / ECharts 折线（canvas 兜底）/ 涨跌榜 */
"use strict";

/* ── Sparkline（纯 SVG，不依赖 ECharts） ─────────────────── */
function renderSparkline(el, series, color) {
  if (!el || !series || series.length === 0) return;
  const vals = series.map(Number).filter((v) => !isNaN(v));
  if (vals.length < 2) {
    el.innerHTML = '<div class="spark-empty">数据不足</div>';
    return;
  }
  const up = vals[vals.length - 1] >= vals[0];
  const c = color || (up ? "#dc2626" : "#16a34a"); // 涨红跌绿
  const W = el.clientWidth || 200, H = 42, P = 3;
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = max - min || 1;
  const px = (i) => P + (i * (W - 2 * P)) / (vals.length - 1);
  const py = (v) => H - P - ((v - min) / span) * (H - 2 * P);
  const pts = vals.map((v, i) => `${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(" ");
  el.innerHTML = `
<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="sparkline">
  <polygon points="${P},${H - P} ${pts} ${W - P},${H - P}" fill="${c}" opacity="0.12"></polygon>
  <polyline points="${pts}" fill="none" stroke="${c}" stroke-width="1.6" stroke-linejoin="round"></polyline>
  <circle cx="${px(vals.length - 1).toFixed(1)}" cy="${py(vals[vals.length - 1]).toFixed(1)}" r="2.2" fill="${c}"></circle>
</svg>`;
}

/* ── ECharts 折线图（无 ECharts 时 canvas 手绘兜底） ─────── */
function renderLine(el, xData, seriesList, opts = {}) {
  if (!el || !xData || xData.length === 0) {
    if (el) el.innerHTML = '<div class="chart-empty">暂无趋势数据</div>';
    return;
  }
  if (window.echarts) {
    const chart = echarts.init(el);
    chart.setOption({
      animation: false,
      grid: { left: 8, right: 8, top: 34, bottom: 6, containLabel: true },
      tooltip: { trigger: "axis", valueFormatter: (v) => (v == null ? "—" : Number(v).toLocaleString("zh-CN")) },
      legend: { top: 0, type: "scroll", textStyle: { fontSize: 11 } },
      xAxis: { type: "category", data: xData, axisLabel: { fontSize: 10, rotate: 30 } },
      yAxis: { type: "value", scale: true, splitLine: { lineStyle: { color: "#eef2f7" } }, axisLabel: { fontSize: 10 } },
      series: seriesList.map((s) => ({
        name: s.name, type: "line", data: s.data, smooth: false,
        symbol: "circle", symbolSize: 4, lineStyle: { width: 1.8 },
        connectNulls: true,
      })),
      color: opts.colors || ["#dc2626", "#2563eb", "#16a34a", "#f59e0b", "#7c3aed", "#0891b2", "#db2777", "#64748b"],
    });
    el._chart = chart;
    return;
  }
  renderCanvasLine(el, xData, seriesList, opts);
}

/* canvas 手绘折线兜底（断网/未加载 ECharts 时保持可用） */
function renderCanvasLine(el, xData, seriesList, opts) {
  const colors = opts.colors || ["#dc2626", "#2563eb", "#16a34a", "#f59e0b", "#7c3aed", "#0891b2"];
  const W = el.clientWidth || 720, H = 300, PL = 52, PR = 10, PT = 14, PB = 26;
  const all = seriesList.flatMap((s) => s.data).filter((v) => v != null);
  if (all.length === 0) { el.innerHTML = '<div class="chart-empty">暂无趋势数据</div>'; return; }
  let min = Math.min(...all), max = Math.max(...all);
  const span = max - min || 1;
  min -= span * 0.05; max += span * 0.05;
  const X = (i) => PL + (i * (W - PL - PR)) / Math.max(1, xData.length - 1);
  const Y = (v) => PT + ((max - v) / (max - min)) * (H - PT - PB);
  let html = `<canvas width="${W}" height="${H}"></canvas>`;
  const legend = seriesList.map((s, i) => `<span class="lg"><i style="background:${colors[i % colors.length]}"></i>${Fmt.esc(s.name)}</span>`).join("");
  html += `<div class="canvas-legend">${legend}</div>`;
  el.innerHTML = html;
  const ctx = el.querySelector("canvas").getContext("2d");
  ctx.strokeStyle = "#eef2f7"; ctx.fillStyle = "#94a3b8"; ctx.font = "10px sans-serif"; ctx.textAlign = "right";
  const ticks = 5;
  for (let t = 0; t <= ticks; t++) {
    const v = max - ((max - min) * t) / ticks, y = PT + ((max - v) / (max - min)) * (H - PT - PB);
    ctx.beginPath(); ctx.moveTo(PL, y); ctx.lineTo(W - PR, y); ctx.stroke();
    ctx.fillText(Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 0 }), PL - 6, y + 3);
  }
  ctx.textAlign = "center";
  const step = Math.max(1, Math.floor(xData.length / 8));
  xData.forEach((d, i) => { if (i % step === 0) ctx.fillText(d.slice(5), X(i), H - 10); });
  seriesList.forEach((s, si) => {
    ctx.strokeStyle = colors[si % colors.length]; ctx.lineWidth = 1.8; ctx.beginPath();
    s.data.forEach((v, i) => {
      if (v == null) return;
      const x = X(i), y = Y(v);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
}

/* 窗口缩放时重绘 ECharts */
window.addEventListener("resize", () => {
  document.querySelectorAll("[data-chart]").forEach((el) => { if (el._chart) el._chart.resize(); });
});

/* ── 涨跌榜表格（数据全部来自后端 _rankings：涨幅榜仅 pct>0、跌幅榜仅 pct<0） ── */
function renderRankTable(el, gainers, losers, opts) {
  if (!el) return;
  opts = opts || {};
  const based = opts.based_on || {};
  const rangeTag = (based.date && based.prev_date)
    ? `<span class="rank-range">对比 ${String(based.prev_date).slice(5)} → ${String(based.date).slice(5)}</span>`
    : "";
  const col = (list, emptyText, noteText) => {
    const rows = (list || []).map((r, i) => `
<tr>
  <td class="rank-no">${i + 1}</td>
  <td><div class="rank-name">${Fmt.esc(r.product)}</div><div class="rank-sub">${Fmt.esc(r.category || "")}</div></td>
  <td class="num">${Fmt.price(r.value)}</td>
  <td class="${Fmt.cls(r.pct)}">${Fmt.pct(r.pct)}</td>
</tr>`).join("");
    const body = rows ||
      `<tr><td colspan="4" class="empty">${emptyText}</td></tr>`;
    const note = (list && list.length > 0 && list.length < 5)
      ? `<tr><td colspan="4" class="rank-note">${noteText.replace("{n}", list.length)}</td></tr>`
      : "";
    return body + note;
  };
  el.innerHTML = `
<div class="rank-col">
  <div class="rank-title up">📈 今日涨幅前5${rangeTag}</div>
  <table class="rank-table"><tbody>${col(gainers, "今日无上涨产品", "今日仅{n}项价格上涨")}</tbody></table>
</div>
<div class="rank-col">
  <div class="rank-title down">📉 今日跌幅前5${rangeTag}</div>
  <table class="rank-table"><tbody>${col(losers, "今日无下跌产品", "今日仅{n}项价格下跌")}</tbody></table>
</div>`;
}
