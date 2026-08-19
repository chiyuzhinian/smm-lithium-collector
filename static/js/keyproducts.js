/* 首页「重点产品价格」模块：tab/搜索/子类渲染 + 趋势弹窗。
   数据全部来自后端 /api/key-products（SQLite 真实记录），前端只负责渲染，
   不做任何补点/插值/伪造；历史不足与暂无数据场景按任务口径显示。 */
"use strict";

const KP = {
  payload: null,
  activeTab: "all",
  query: "",
  modalKey: null,
  modalRange: "7d",
  loading: false,

  async init() {
    this.payload = await API.get("/api/key-products");
    this.bindTabs();
    this.bindSearch();
    this.bindModal();
    this.render();
  },

  /* ── tab 切换 + 搜索 ───────────────────────────────── */
  bindTabs() {
    const tabs = document.getElementById("kp-tabs");
    tabs.addEventListener("click", (e) => {
      const btn = e.target.closest(".kp-tab");
      if (!btn) return;
      tabs.querySelectorAll(".kp-tab").forEach((t) => t.classList.toggle("active", t === btn));
      this.activeTab = btn.dataset.tab;
      this.render();
    });
  },

  bindSearch() {
    const box = document.getElementById("kp-search");
    box.addEventListener("input", () => {
      this.query = box.value.trim().toLowerCase();
      this.render();
    });
  },

  visibleProducts() {
    let list = this.payload.products || [];
    if (this.activeTab !== "all") {
      list = list.filter((p) => p.business_category === this.activeTab);
    }
    if (this.query) {
      list = list.filter((p) =>
        String(p.display_name || "").toLowerCase().includes(this.query) ||
        String(p.full_name || "").toLowerCase().includes(this.query));
    }
    return list;
  },

  /* ── 分组渲染（保留后端配置顺序） ──────────────────── */
  render() {
    const el = document.getElementById("kp-body");
    if (!this.payload) return;
    const visible = this.visibleProducts();
    if (!visible.length) {
      el.innerHTML = '<div class="empty-state"><div class="empty-icon">🔍</div><div>暂无匹配产品</div></div>';
      return;
    }
    const groups = (this.payload.groups || []).filter((g) =>
      this.activeTab === "all" || g.key === this.activeTab);
    const html = [];
    for (const g of groups) {
      for (const sub of g.subcategories || []) {
        const cards = (sub.products || []).filter((p) => visible.some((x) => x.key === p.key));
        if (!cards.length) continue;
        html.push(`<div class="kp-subcat">${Fmt.esc(sub.name)}<span class="count">${cards.length} 个产品</span></div>`);
        html.push('<div class="metric-grid kp-grid">');
        html.push(cards.map((p) => this.cardHtml(p)).join(""));
        html.push("</div>");
      }
    }
    el.innerHTML = html.join("");
    el.querySelectorAll(".metric-spark[data-spark]").forEach((d) => {
      try { renderSparkline(d, JSON.parse(d.dataset.spark)); } catch (e) { /* ignore */ }
    });
    el.querySelectorAll(".kp-trend-btn:not(:disabled)").forEach((b) => {
      b.addEventListener("click", () => this.openModal(b.dataset.key));
    });
  },

  /* ── 产品卡片（最新真实价 / 较上次 / 近7日真实 spark / 双日期） ── */
  cardHtml(p) {
    const sparkVals = (p.spark_points || []).map((x) => x.value);
    let valueHtml;
    if (p.value === null || p.value === undefined) {
      valueHtml = '<div class="metric-value">暂无数据</div>';
    } else {
      valueHtml = `<div class="metric-value">${Fmt.price(p.value)}<span class="metric-unit">${Fmt.esc(p.unit || "")}</span></div>`;
    }
    let changeHtml;
    if (p.change_pct === null || p.change_pct === undefined) {
      changeHtml = '<div class="metric-change"><span class="flat">较上次 —</span></div>';
    } else {
      const prevLabel = p.prev_date ? "(" + String(p.prev_date).slice(5) + ") " : "";
      changeHtml = `<div class="metric-change"><span class="${Fmt.cls(p.change_pct)}">较上次 ${prevLabel}${Fmt.pct(p.change_pct)}</span></div>`;
    }
    let sparkHtml;
    if (sparkVals.length >= 2) {
      sparkHtml = `<div class="metric-spark" data-spark="${Fmt.esc(JSON.stringify(sparkVals))}"></div>`;
    } else if (sparkVals.length === 1) {
      sparkHtml = '<div class="spark-empty">历史数据不足</div>';
    } else {
      sparkHtml = '<div class="spark-empty">暂无数据</div>';
    }
    const disabledAttr = p.has_history ? "" : "disabled";
    const btnTitle = p.has_history ? "查看近7天/近30天真实价格趋势" : "历史数据不足";
    return `
<div class="metric-card kp-card">
  <div class="metric-head">
    <span class="metric-name" title="${Fmt.esc(p.full_name || p.display_name)}">${Fmt.esc(p.display_name)}</span>
    <span class="group-chip">${Fmt.esc(p.subcategory || "")}</span>
  </div>
  ${valueHtml}
  ${changeHtml}
  ${sparkHtml}
  <div class="metric-time">数据日期：${Fmt.esc(p.price_date || "—")}${p.is_stale ? '<span class="label-warn">数据较旧</span>' : ""} · 更新时间：${Fmt.esc(p.updated_at || "—")}</div>
  <button class="btn btn-small kp-trend-btn" data-key="${Fmt.esc(p.key)}" ${disabledAttr} title="${btnTitle}">查看趋势</button>
</div>`;
  },

  /* ── 趋势弹窗（近7天/近30天，按需加载，不缓存） ──────── */
  bindModal() {
    const overlay = document.getElementById("kp-modal");
    document.getElementById("kp-modal-close").addEventListener("click", () => this.closeModal());
    overlay.addEventListener("click", (e) => { if (e.target === overlay) this.closeModal(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") this.closeModal(); });
    overlay.querySelectorAll(".range-btn").forEach((b) => {
      b.addEventListener("click", () => {
        if (b.classList.contains("active")) return;
        overlay.querySelectorAll(".range-btn").forEach((x) => x.classList.toggle("active", x === b));
        this.modalRange = b.dataset.range;
        this.loadHistory();
      });
    });
  },

  openModal(key) {
    const p = (this.payload.products || []).find((x) => x.key === key);
    if (!p) return;
    this.modalKey = key;
    this.modalRange = "7d";
    const overlay = document.getElementById("kp-modal");
    overlay.querySelectorAll(".range-btn").forEach((x) => x.classList.toggle("active", x.dataset.range === "7d"));
    document.getElementById("kp-modal-title").textContent = p.display_name;
    document.getElementById("kp-modal-sub").textContent = p.full_name || "";
    overlay.hidden = false;
    // 先显示容器再 init 图表，避免隐藏元素下 ECharts 得到 0 宽
    requestAnimationFrame(() => this.loadHistory());
  },

  closeModal() {
    const overlay = document.getElementById("kp-modal");
    if (overlay.hidden) return;
    overlay.hidden = true;
    this.modalKey = null;
    const chartEl = document.getElementById("kp-chart");
    if (chartEl._chart) { chartEl._chart.dispose(); chartEl._chart = null; }
  },

  async loadHistory() {
    if (!this.modalKey || this.loading) return;
    const overlay = document.getElementById("kp-modal");
    if (overlay.hidden) return;
    this.loading = true;
    const chartEl = document.getElementById("kp-chart");
    const foot = document.getElementById("kp-modal-foot");
    chartEl.innerHTML = '<div class="chart-empty">加载中…</div>';
    try {
      const url = "/api/key-products/history?key=" + encodeURIComponent(this.modalKey) +
        "&range=" + encodeURIComponent(this.modalRange);
      const d = await API.get(url, 0); // 趋势数据每次拉最新，不进 30s 缓存
      if (overlay.hidden || this.modalKey === null) return;
      const rangeLabel = this.modalRange === "7d" ? "近7天" : "近30天";
      const data = (d && d.data) || [];
      if (!data.length) {
        chartEl.innerHTML = `<div class="chart-empty">${rangeLabel}暂无报价</div>`;
        foot.textContent = "";
        return;
      }
      const xData = data.map((x) => x.date);
      const vals = data.map((x) => x.price);
      if (vals.length < 2) {
        chartEl.innerHTML = '<div class="chart-empty">历史数据不足</div>';
        foot.textContent = `最新数据日期：${Fmt.esc(d.latest_date || "—")} · 数据单位：${Fmt.esc(d.unit || "—")}`;
        return;
      }
      if (chartEl._chart) { chartEl._chart.dispose(); chartEl._chart = null; }
      renderLine(chartEl, xData, [{ name: d.display_name || "", data: vals }]);
      foot.textContent = `最新数据日期：${Fmt.esc(d.latest_date || "—")} · 数据单位：${Fmt.esc(d.unit || "—")} · 共 ${data.length} 个真实数据点`;
    } catch (e) {
      if (overlay.hidden) return;
      chartEl.innerHTML = '<div class="chart-empty">趋势加载失败：' + Fmt.esc(e.message || "") + "</div>";
      foot.textContent = "";
    } finally {
      this.loading = false;
    }
  },
};

document.addEventListener("DOMContentLoaded", () => {
  KP.init().catch((e) => {
    toast("重点产品加载失败：" + e.message, "error");
    const el = document.getElementById("kp-body");
    if (el) el.innerHTML = '<div class="empty-state"><div class="empty-icon">⚠️</div><div>数据加载失败，请稍后重试</div></div>';
  });
});
