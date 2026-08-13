/* 历史数据中心：起始~结束日期 + 业务分组 + 分类 + 关键词 四条件 AND 组合检索 */
"use strict";

let CATS = [];

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const [hist, cats] = await Promise.all([API.get("/api/history"), API.get("/api/categories")]);
    CATS = cats.groups || [];
    renderGroups(CATS);
    renderCats(CATS);
    // 日期范围默认 = 实际历史数据的 min/max 业务日期（自动识别，不写死）
    if (hist.date_min) document.getElementById("date-from").value = hist.date_min;
    if (hist.date_max) document.getElementById("date-to").value = hist.date_max;
    // 副标题展示真实数据起始日（来自 /api/history 的 date_min，不硬编码）
    const sub = document.querySelector(".page-date");
    if (sub && hist.date_min) {
      sub.textContent = `历史归档覆盖 ${hist.date_min} 起，可检索每日总表与分类 CSV，并支持历史趋势查看`;
    }
    renderArchiveBtns(hist);
    await loadPage(1);
  } catch (e) {
    toast("历史数据加载失败：" + e.message, "error");
  }

  const reload = () => loadPage(1);
  document.getElementById("date-from").addEventListener("change", reload);
  document.getElementById("date-to").addEventListener("change", reload);
  document.getElementById("group-select").addEventListener("change", () => {
    renderCats(CATS, document.getElementById("group-select").value);
    reload();
  });
  document.getElementById("category-select").addEventListener("change", reload);
  let timer = null;
  document.getElementById("search-input").addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(reload, 350);
  });
});

function renderGroups(groups) {
  const el = document.getElementById("group-select");
  el.innerHTML = '<option value="">全部分组</option>' +
    groups.map((g) => `<option value="${Fmt.esc(g.key)}">${Fmt.esc(g.icon)} ${Fmt.esc(g.name)}</option>`).join("");
}

function renderCats(groups, groupKey) {
  const el = document.getElementById("category-select");
  const picked = el.value;
  let opts = '<option value="">全部分类</option>';
  groups.forEach((g) => {
    if (groupKey && g.key !== groupKey) return;
    opts += `<optgroup label="${Fmt.esc(g.name)}">` +
      g.categories.map((c) => `<option value="${Fmt.esc(c.category)}">${Fmt.esc(c.category)}</option>`).join("") +
      "</optgroup>";
  });
  el.innerHTML = opts;
  el.value = picked;
}

function renderArchiveBtns(hist) {
  // 正式汇总档案（固定展示，不受日期/分类/关键词筛选影响）：历史总表 + 固定汇总
  const el = document.getElementById("history-summary-btns");
  const hs = hist.history_summary || {};
  const fs = hist.fixed_summary || {};
  const parts = [];
  parts.push(hs.exists
    ? `<a class="btn btn-primary" href="${hs.path}" download>⬇ 历史总表下载</a>`
    : '<span class="btn btn-disabled">历史总表未生成</span>');
  parts.push(fs.exists
    ? `<a class="btn btn-primary" href="${fs.path}" download>⬇ 固定汇总下载</a>`
    : '<span class="btn btn-disabled">固定汇总未生成</span>');
  el.innerHTML = parts.join("");
}

async function loadPage(page) {
  const params = new URLSearchParams({ page: String(page), page_size: "50" });
  const from = document.getElementById("date-from").value;
  const to = document.getElementById("date-to").value;
  const group = document.getElementById("group-select").value;
  const category = document.getElementById("category-select").value;
  const q = document.getElementById("search-input").value.trim();
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  if (group) params.set("group", group);
  if (category) params.set("category", category);
  if (q) params.set("q", q);
  try {
    const d = await API.get("/api/history?" + params.toString(), 5000);
    renderTable(d.files);
    renderPager(d);
    document.getElementById("result-count").textContent =
      `共 ${d.total} 个文件 · 第 ${d.page} 页 / ${Math.max(1, Math.ceil(d.total / d.page_size))} 页` +
      ` · 数据日期范围 ${d.date_min || "—"} ~ ${d.date_max || "—"}`;
  } catch (e) {
    toast("查询失败：" + e.message, "error");
  }
}

function renderTable(files) {
  const el = document.getElementById("file-tbody");
  el.innerHTML = files.map((f) => `
<tr>
  <td><div class="file-name" title="${Fmt.esc(f.path)}">${Fmt.esc(f.name)}</div></td>
  <td><span class="recent-date">${Fmt.esc(f.date || "—")}</span></td>
  <td>${Fmt.esc(f.kind || "—")}</td>
  <td>${Fmt.esc(f.category || "—")}</td>
  <td><span class="group-chip">${Fmt.esc(f.group_name || "—")}</span></td>
  <td>${Fmt.size(f.size)}</td>
  <td>${Fmt.esc(f.modified || "—")}</td>
  <td><a class="btn btn-small" href="${f.path}" download>下载</a></td>
</tr>`).join("") || '<tr><td colspan="8"><div class="empty-state">无匹配文件</div></td></tr>';
}

function renderPager(d) {
  const el = document.getElementById("pager");
  const totalPages = Math.max(1, Math.ceil(d.total / d.page_size));
  if (totalPages <= 1) { el.innerHTML = ""; return; }
  const btn = (p, label, dis) =>
    `<button class="btn btn-small ${dis ? "btn-disabled" : ""}" ${dis ? "disabled" : ""} data-page="${p}">${label}</button>`;
  el.innerHTML = btn(d.page - 1, "← 上一页", d.page <= 1) +
    `<span class="pager-info">${d.page} / ${totalPages}</span>` +
    btn(d.page + 1, "下一页 →", d.page >= totalPages);
  el.querySelectorAll("button[data-page]").forEach((b) =>
    b.addEventListener("click", () => loadPage(parseInt(b.dataset.page, 10))));
}
