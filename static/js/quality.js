/* 数据质量面板：今日状态 / 固定汇总 / 近14日明细 / 缺口清单 */
"use strict";

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const d = await API.get("/api/quality");
    renderToday(d.today);
    renderFixed(d.fixed_summary);
    renderHistory(d.history);
    renderGaps(d.gaps);
  } catch (e) {
    toast("质量数据加载失败：" + e.message, "error");
  }
});

function renderToday(t) {
  const el = document.getElementById("quality-today");
  if (!t || !t.date) { el.innerHTML = '<div class="empty-state">暂无数据状态 manifest</div>'; return; }
  const admitted = t.fixed_summary_admitted;
  const row = (label, value, cls) => `
<div class="q-stat"><div class="q-stat-value ${cls || ""}">${value}</div><div class="q-stat-label">${label}</div></div>`;
  el.innerHTML = `
<div class="q-head">
  <span class="q-date">${Fmt.esc(t.date)}</span>
  <span class="status-badge ${Fmt.badge(t.collection_status)}">采集状态：${Fmt.esc(t.collection_status || "—")}</span>
  <span class="status-badge ${admitted ? "ok" : "warn"}">${admitted ? "✅ 已正式入固定汇总" : "⚠️ 未入正式固定汇总（临时快照）"}</span>
</div>
<div class="q-stats">
  ${row("预期分类", t.expected ?? "—", "")}
  ${row("成功分类", t.success ?? "—", "ok")}
  ${row("失败分类", t.failed ?? "—", t.failed > 0 ? "bad" : "")}
  ${row("valid 行", t.valid ?? "—", "")}
  ${row("warning 行", t.warning ?? "—", "")}
  ${row("invalid 行", t.invalid ?? "—", t.invalid > 0 ? "bad" : "")}
  ${row("日期对齐率", t.date_alignment_ratio == null ? "—" : (t.date_alignment_ratio * 100).toFixed(1) + "%", t.date_alignment_ratio >= 0.8 ? "ok" : "warn")}
</div>
${(t.reasons || []).length ? `<div class="q-reasons"><ul>${t.reasons.map((r) => `<li>⚠️ ${Fmt.esc(r)}</li>`).join("")}</ul></div>` : ""}`;
}

function renderFixed(f) {
  const el = document.getElementById("quality-fixed");
  const snaps = f.temp_snapshots || [];
  el.innerHTML = `
<div class="fixed-card">
  <div class="fixed-status">
    <div class="fixed-updated">正式固定汇总文件：${f.formal_exists ? "存在" : "未生成"}</div>
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
  el.innerHTML = rows.map((r) => `
<tr>
  <td>${Fmt.esc(r.date)}</td>
  <td><span class="status-badge ${Fmt.badge(r.status)}">${Fmt.esc(r.status || "—")}</span></td>
  <td>${r.success ?? "—"} / ${r.expected ?? "—"}</td>
  <td>${r.admitted ? '<span class="admit ok">✅ 已入</span>' : '<span class="admit warn">❌ 未入</span>'}</td>
  <td>${Fmt.esc(r.formal_status || "—")}</td>
</tr>`).join("") || '<tr><td colspan="5"><div class="empty-state">暂无 manifest 记录</div></td></tr>';
}

function renderGaps(gaps) {
  const el = document.getElementById("gaps-list");
  el.innerHTML = (gaps || []).map((g) => `
<div class="gap-item"><span class="gap-date">${Fmt.esc(g.date)}</span><span class="gap-reason">${Fmt.esc(g.reason)}</span></div>`).join("")
    || '<div class="empty-state">✅ 无数据缺口</div>';
}
