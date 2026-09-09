/* 数据质量面板：当前质量状态 / 固定汇总 / 近14日明细 / 缺口清单
   已返回数据必须渲染；失败显失败态；无记录显明确空态（不把接口失败当正常） */
"use strict";

const Q_IDS = ["quality-today", "quality-fixed", "quality-tbody", "gaps-list"];

function renderError() {
  const retry = `<div><button class="btn sm" style="margin-top:10px" onclick="location.reload()">重试</button></div>`;
  document.getElementById("quality-today").innerHTML =
    `<div class="quality-state">⚠️ 数据质量接口加载失败，无法显示当前状态。${retry}</div>`;
  document.getElementById("quality-fixed").innerHTML =
    `<div class="quality-state">⚠️ 固定汇总状态加载失败。${retry}</div>`;
  document.getElementById("quality-tbody").innerHTML =
    `<tr><td colspan="5"><div class="quality-state">⚠️ 近 14 日明细加载失败。${retry}</div></td></tr>`;
  document.getElementById("gaps-list").innerHTML =
    `<div class="quality-state">⚠️ 缺口清单加载失败。${retry}</div>`;
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const d = await API.get("/api/quality");
    renderToday(d.today, d.meta);
    renderFixed(d.fixed_summary);
    renderHistory(d.history);
    renderGaps(d.gaps);
  } catch (e) {
    renderError();
    toast("质量数据加载失败：" + e.message, "error");
  }
});

function renderToday(t, meta) {
  const el = document.getElementById("quality-today");
  if (!t || !t.date) {
    el.innerHTML = `<div class="quality-state">暂无数据状态 manifest（今日采集尚未生成或暂无记录）。</div>`;
    return;
  }
  const admitted = t.fixed_summary_admitted;
  const row = (label, value, cls) => `
<div class="q-stat"><div class="q-stat-value ${cls || ""}">${value}</div><div class="q-stat-label">${label}</div></div>`;
  el.innerHTML = `
<div class="q-head">
  <span class="q-date">${Fmt.esc(t.date)}</span>
  <span class="status-badge ${Fmt.badge(t.collection_status)}">采集状态：${Fmt.esc(t.collection_status || "—")}</span>
  <span class="status-badge ${admitted ? "ok" : "warn"}">${admitted ? "✓ 已正式入固定汇总" : "⚠ 未入正式固定汇总（临时快照）"}</span>
  <span class="section-note">更新于 ${Fmt.esc((meta && meta.generated_at) || "—")}</span>
</div>
<div class="q-stats">
  ${row("预期分类", t.expected ?? "—", "")}
  ${row("成功分类", t.success ?? "—", "ok")}
  ${row("失败分类", t.failed ?? "—", t.failed > 0 ? "bad" : "")}
  ${row("valid 行", t.valid ?? "—", "")}
  ${row("warning 行", t.warning ?? "—", "")}
  ${row("invalid 行", t.invalid ?? "—", t.invalid > 0 ? "bad" : "")}
  ${row("日期对齐率", t.date_alignment_ratio == null ? "—" : (t.date_alignment_ratio * 100).toFixed(1) + "%", t.date_alignment_ratio >= 0.8 ? "ok" : "warn")}
  ${t.data_date && t.data_date !== t.date ? row("数据日期", Fmt.esc(t.data_date), "ok") : ""}
</div>
${(t.reasons || []).length ? `<div class="q-reasons"><ul>${t.reasons.map((r) => `<li>⚠ ${Fmt.esc(r)}</li>`).join("")}</ul></div>` : ""}`;
}

function renderFixed(f) {
  const el = document.getElementById("quality-fixed");
  if (!f) {
    el.innerHTML = `<div class="quality-state">暂无固定汇总状态。</div>`;
    return;
  }
  const snaps = f.temp_snapshots || [];
  el.innerHTML = `
<div class="fixed-card">
  <div class="fixed-status">
    <div class="fixed-updated">正式固定汇总文件：<b>${f.formal_exists ? "存在" : "未生成"}</b></div>
    <div class="fixed-updated">最近更新：${Fmt.esc(f.formal_updated_at || "—")}</div>
  </div>
  <div class="fixed-links">
    ${f.formal_exists ? `<a class="btn btn-small" href="${f.formal_file_path}" download>⬇ 正式固定汇总</a>` : ""}
  </div>
  ${snaps.length ? `<div class="fixed-warnings"><div class="snap-title">临时快照（部分成功时生成，仅供参考，不覆盖正式文件）：</div>
    <ul>${snaps.map((s) => `<li><a href="${s.path}" download>${Fmt.esc(s.name)}</a> <span class="snap-time">${Fmt.esc(s.modified)}</span></li>`).join("")}</ul></div>` : ""}
</div>`;
}

function renderHistory(rows) {
  const el = document.getElementById("quality-tbody");
  if (!rows || !rows.length) {
    el.innerHTML = `<tr><td colspan="5"><div class="quality-state">暂无 manifest 记录。</div></td></tr>`;
    return;
  }
  el.innerHTML = rows.map((r) => `
<tr>
  <td>${Fmt.esc(r.date)}</td>
  <td><span class="status-badge ${Fmt.badge(r.status)}">${Fmt.esc(r.status || "—")}</span></td>
  <td>${r.success ?? "—"} / ${r.expected ?? "—"}</td>
  <td>${r.admitted ? '<span class="admit ok">✓ 已入</span>' : '<span class="admit warn">✕ 未入</span>'}</td>
  <td>${Fmt.esc(r.formal_status || "—")}</td>
</tr>`).join("");
}

function renderGaps(gaps) {
  const el = document.getElementById("gaps-list");
  if (!gaps || !gaps.length) {
    el.innerHTML = `<div class="quality-state">✓ 无数据缺口。</div>`;
    return;
  }
  el.innerHTML = gaps.map((g) => `
<div class="gap-item"><span class="gap-date">${Fmt.esc(g.date)}</span><span class="gap-reason">${Fmt.esc(g.reason)}</span></div>`).join("");
}
