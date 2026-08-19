/* 系统运维中心（仅管理员可访问；服务端已做 role 校验） */
"use strict";

(function () {
  const POLL_MS = 30000; // 30 秒轮询，与服务端聚合缓存同频
  const TABS = ["overview", "tasks", "quality", "resources", "logs", "users", "security"];
  const CARD_ORDER = ["web", "collector", "data_date", "validation", "fixed_summary", "disk", "mem", "cpu"];

  /* ── Tab 切换 ── */
  function showTab(name) {
    TABS.forEach((t) => {
      const panel = document.getElementById("panel-" + t);
      if (panel) panel.hidden = t !== name;
      const link = document.querySelector(`.admin-nav a[data-tab="${t}"]`);
      if (link) link.classList.toggle("active", t === name);
    });
    if (name === "tasks") loadTasks();
    if (name === "quality") loadQuality();
    if (name === "resources") loadResources();
    if (name === "logs") initLogs();
    if (name === "users") loadUsers();
    if (name === "security") loadAudit();
  }
  document.querySelectorAll(".admin-nav a").forEach((a) => {
    a.addEventListener("click", (e) => { e.preventDefault(); showTab(a.dataset.tab); });
  });

  /* ── 状态渲染（图标+文字+颜色，绝不只用颜色） ── */
  function statusBadge(status, text) {
    const label = text || Fmt.statusText(status);
    return `<span class="badge-${status}">${Fmt.statusIcon(status)} ${Fmt.esc(label)}</span>`;
  }
  const TASK_STATUS_TEXT = { ok: "成功", warn: "警告", crit: "失败" };

  function renderCard(c) {
    let detail = Fmt.esc(c.detail || "");
    if (c.key === "web" && c.process_uptime_seconds !== null && c.process_uptime_seconds !== undefined) {
      detail = Fmt.esc(c.detail) + " · Web 运行 " + Fmt.uptime(c.process_uptime_seconds);
    }
    return `<div class="metric-card">
      <div class="mc-name">${Fmt.esc(c.name)} ${statusBadge(c.status)}</div>
      <div class="mc-value">${Fmt.esc(c.value)}</div>
      <div class="mc-detail">${detail}</div>
      <div class="mc-time">检查于 ${Fmt.esc(c.last_checked_at || "—")}</div>
    </div>`;
  }

  /* ── 运维总览 ── */
  async function loadOverview() {
    try {
      const d = await API.get("/api/admin/overview", 0);
      renderOverview(d);
    } catch (e) {
      const badge = document.getElementById("overall-badge");
      badge.textContent = "加载失败";
      badge.className = "overall-badge overall-crit";
    }
  }

  function renderOverview(d) {
    const badge = document.getElementById("overall-badge");
    badge.textContent = d.status === "ok" ? "● 系统正常" : d.status === "warn" ? "● 存在警告" : "● 系统异常";
    badge.className = "overall-badge overall-" + d.status;
    document.getElementById("last-checked").textContent = "最后检查 " + Fmt.esc(d.last_checked_at || "");

    // 告警列表
    const alerts = d.alerts || [];
    document.getElementById("alerts-box").innerHTML = alerts.length
      ? '<div class="alert-box">' + alerts.map((a) =>
          `<div class="alert-item alert-${a.level === "crit" ? "crit" : "warn"}">${Fmt.esc(a.message)}</div>`).join("") + "</div>"
      : '<div class="alert-box"><div class="alert-item" style="background:#f0fdf4;border-color:#bbf7d0;color:#166534;">✅ 最近 24 小时无严重异常</div></div>';

    // 核心状态卡片
    const byKey = Object.fromEntries((d.cards || []).map((c) => [c.key, c]));
    document.getElementById("cards-grid").innerHTML =
      CARD_ORDER.filter((k) => byKey[k]).map((k) => renderCard(byKey[k])).join("");

    // 今日采集状态条
    const tc = d.today_collection || {};
    if (tc.date) {
      const exp = tc.expected || 0;
      const bars = [
        { label: "预期分类", v: exp, max: Math.max(exp, 1), cls: "ok" },
        { label: "成功", v: tc.success || 0, max: Math.max(exp, 1), cls: "ok" },
        { label: "Warning", v: tc.warning || 0, max: Math.max(exp, 1), cls: "warn" },
        { label: "Fail", v: (tc.failed || 0) + (tc.invalid || 0), max: Math.max(exp, 1), cls: "crit" },
      ];
      const missing = (tc.missing_categories && tc.missing_categories.length)
        ? `<p class="section-note">⚠️ 缺失分类：${Fmt.esc(tc.missing_categories.join("、"))}</p>` : "";
      document.getElementById("today-collection").innerHTML =
        `<p class="section-note">业务日期 ${Fmt.esc(tc.date)} · 有效 ${Fmt.num(tc.valid)} · Warning ${Fmt.num(tc.warning)} · 无效 ${Fmt.num(tc.invalid)}</p>` +
        bars.map((b) => {
          const pct = Math.min(100, Math.round((b.v / b.max) * 100));
          return `<div class="bar-row"><span class="bar-label">${b.label}</span>` +
            `<span class="bar-track"><span class="bar-fill ${b.cls}" style="width:${pct}%"></span></span>` +
            `<span class="bar-num">${Fmt.num(b.v)}</span></div>`;
        }).join("") + missing;
    } else {
      document.getElementById("today-collection").innerHTML =
        '<div class="empty-state"><div class="empty-icon">⏳</div><div>今日暂无采集记录</div></div>';
    }

    // 最近任务
    const tasks = d.recent_tasks || [];
    document.getElementById("recent-tasks").innerHTML = tasks.length
      ? `<table class="admin-table"><thead><tr><th>任务</th><th>状态</th><th>最近执行</th><th>耗时</th><th>摘要</th></tr></thead><tbody>` +
        tasks.map((t) => `<tr><td>${Fmt.esc(t.name)}</td><td>${statusBadge(t.status, TASK_STATUS_TEXT[t.status])}</td>` +
          `<td>${Fmt.esc(t.last_run || "—")}</td><td>${Fmt.esc(t.duration || "—")}</td><td>${Fmt.esc(t.summary || "")}</td></tr>`).join("") +
        "</tbody></table>"
      : '<div class="empty-state"><div class="empty-icon">🗓</div><div>暂无任务记录</div></div>';

    // 最近异常（仅摘要；详情见「运行日志」页）
    const errs = d.recent_errors || [];
    document.getElementById("recent-errors").innerHTML = errs.length
      ? `<table class="admin-table"><thead><tr><th>来源</th><th>信息</th></tr></thead><tbody>` +
        errs.map((e2) => `<tr><td>${Fmt.esc(e2.source)}</td><td>${Fmt.esc(e2.message)}</td></tr>`).join("") + "</tbody></table>"
      : '<div class="empty-state"><div class="empty-icon">✅</div><div>最近 24 小时无严重异常</div></div>';
  }

  /* ── 采集任务 ── */
  async function loadTasks() {
    try {
      const d = await API.get("/api/admin/tasks", 0);
      document.getElementById("tasks-table").innerHTML =
        `<table class="admin-table"><thead><tr><th>任务</th><th>调度</th><th>下次执行</th><th>最近执行</th><th>状态</th><th>耗时</th><th>摘要</th></tr></thead><tbody>` +
        (d.tasks || []).map((t) => `<tr>
          <td>${Fmt.esc(t.name)}</td>
          <td>${Fmt.esc(t.cron || "跟随兜底任务")}</td>
          <td>${Fmt.esc(t.next_run || "—")}</td>
          <td>${Fmt.esc(t.last_run || "—")}</td>
          <td>${statusBadge(t.status, TASK_STATUS_TEXT[t.status])}</td>
          <td>${Fmt.esc(t.duration || "—")}</td>
          <td>${Fmt.esc(t.summary || "")}</td></tr>`).join("") + "</tbody></table>";
    } catch (e) { /* api.js 已处理会话跳转 */ }
  }

  /* ── 数据质量 ── */
  async function loadQuality() {
    try {
      const d = await API.get("/api/admin/data-quality", 0);
      const q = d.quality;
      if (!q) {
        document.getElementById("quality-box").innerHTML =
          '<div class="empty-state"><div class="empty-icon">⏳</div><div>暂无验证记录</div></div>';
        document.getElementById("quality-date").textContent = "";
        return;
      }
      const counts = q.counts || {};
      document.getElementById("quality-date").textContent =
        `业务日期 ${q.date || "—"} · 生成于 ${q.generated_at || "—"} · ` +
        `通过 ${counts.info || 0} · 警告 ${counts.warning || 0} · 失败 ${counts.error || 0}`;
      const vs = { PASS: "ok", WARNING: "warn", FAIL: "crit" }[q.verdict] || "warn";
      const colors = {
        ok: ["#f0fdf4", "#bbf7d0", "#166534"],
        warn: ["#fffbeb", "#fcd34d", "#92400e"],
        crit: ["#fef2f2", "#fca5a5", "#991b1b"],
      };
      const [bg, bd, fg] = colors[vs];
      const issues = q.issues || [];
      const issueRows = issues.length
        ? `<h3 class="section-note">告警 / 失败详情</h3><table class="admin-table"><thead><tr><th>层级</th><th>级别</th><th>信息</th><th>详情</th></tr></thead><tbody>` +
          issues.map((i) => `<tr><td>${Fmt.esc(i.layer)}</td>` +
            `<td>${statusBadge(i.level === "error" ? "crit" : "warn", i.level === "error" ? "失败" : "警告")}</td>` +
            `<td>${Fmt.esc(i.message)}</td><td class="quality-detail">${Fmt.esc(i.detail || "")}</td></tr>`).join("") +
          "</tbody></table>" : "";
      document.getElementById("quality-box").innerHTML =
        `<div class="alert-item" style="background:${bg};border-color:${bd};color:${fg};margin-bottom:1rem;">` +
        `数据质量：${Fmt.esc(q.verdict)} ${statusBadge(vs)}</div>` +
        `<table class="admin-table"><thead><tr><th>检查项</th><th>状态</th><th>摘要</th></tr></thead><tbody>` +
        (q.layers || []).map((l) => {
          const ls = l.status === "fail" ? "crit" : (l.status === "warn" || l.status === "skipped") ? "warn" : "ok";
          const lt = l.status === "fail" ? "失败" : l.status === "warn" ? "警告" : l.status === "skipped" ? "跳过" : "通过";
          return `<tr><td>${Fmt.esc(l.name)}</td><td>${statusBadge(ls, lt)}</td><td>${Fmt.esc(l.summary || "")}</td></tr>`;
        }).join("") + "</tbody></table>" + issueRows;
    } catch (e) { /* ignore */ }
  }

  /* ── 系统资源 ── */
  function loadResources() {
    API.get("/api/admin/overview", 30000).then((d) => {
      const byKey = Object.fromEntries((d.cards || []).map((c) => [c.key, c]));
      const web = byKey.web || {};
      const items = [
        ["CPU", byKey.cpu, (byKey.cpu || {}).value, (byKey.cpu || {}).detail],
        ["内存", byKey.mem, (byKey.mem || {}).value, (byKey.mem || {}).detail],
        ["磁盘", byKey.disk, (byKey.disk || {}).value, (byKey.disk || {}).detail],
        ["Linux 运行时间", {}, Fmt.uptime(web.linux_uptime_seconds), "服务器已运行"],
        ["Web 服务运行时间", {}, Fmt.uptime(web.process_uptime_seconds), "端口 " + Fmt.esc(web.port)],
      ];
      document.getElementById("resources-grid").innerHTML = items.map(([name, card, value, detail]) =>
        `<div class="metric-card">
          <div class="mc-name">${Fmt.esc(name)} ${card && card.status ? statusBadge(card.status) : ""}</div>
          <div class="mc-value">${Fmt.esc(value)}</div>
          <div class="mc-detail">${Fmt.esc(detail || "")}</div>
          <div class="mc-time">检查于 ${Fmt.esc((card || web).last_checked_at || "—")}</div>
        </div>`).join("");
    }).catch(() => {});
  }

  /* ── 运行日志（白名单） ── */
  const LOG_NAMES = [
    { key: "app", label: "采集日志 collector" },
    { key: "error", label: "错误日志 error" },
    { key: "cron", label: "cron 输出" },
    { key: "metals", label: "铜铝镍补采输出" },
    { key: "validation", label: "验证追加日志" },
  ];
  let logsInited = false;
  function initLogs() {
    if (logsInited) return;
    logsInited = true;
    document.getElementById("log-name").innerHTML =
      LOG_NAMES.map((n) => `<option value="${n.key}">${n.label}</option>`).join("");
    document.getElementById("log-day").innerHTML =
      Array.from({ length: 7 }, (_, i) => `<option value="${i}">${i === 0 ? "今天" : "前 " + i + " 天"}</option>`).join("");
    document.getElementById("log-load").addEventListener("click", loadLog);
    document.getElementById("log-name").addEventListener("change", loadLog);
    document.getElementById("log-day").addEventListener("change", loadLog);
    loadLog();
  }
  async function loadLog() {
    const name = document.getElementById("log-name").value;
    const day = document.getElementById("log-day").value;
    try {
      const d = await API.get(`/api/admin/logs?name=${encodeURIComponent(name)}&day=${day}&lines=200`, 0);
      if (!d.ok) {
        document.getElementById("log-view").textContent = d.error || "读取失败";
        document.getElementById("log-path").textContent = "";
        return;
      }
      document.getElementById("log-path").textContent =
        "文件：" + d.path + (d.exists ? "" : "（当日文件不存在）");
      document.getElementById("log-view").textContent = (d.lines || []).join("\n") || "（无内容）";
    } catch (e) { /* ignore */ }
  }

  /* ── 用户管理（服务端已做 role 校验；此处仅渲染） ── */
  const ROLE_TEXT = { user: "普通用户", admin: "管理员" };
  async function loadUsers() {
    try {
      const d = await API.get("/api/admin/users", 0);
      const rows = d.users || [];
      document.getElementById("users-table").innerHTML = rows.length
        ? `<table class="admin-table"><thead><tr><th>用户名</th><th>角色</th><th>状态</th>` +
          `<th>注册时间</th><th>最近登录</th><th>操作</th></tr></thead><tbody>` +
          rows.map((u) => {
            const status = u.is_active
              ? `<span class="badge-ok">✔ 正常</span>`
              : `<span class="badge-warn">⏸ 已停用</span>`;
            const ops = [];
            if (u.is_active) {
              ops.push(`<button class="btn btn-small" data-op="disable" data-id="${u.id}">停用</button>`);
            } else {
              ops.push(`<button class="btn btn-small" data-op="enable" data-id="${u.id}">启用</button>`);
            }
            ops.push(`<button class="btn btn-small" data-op="reset_password" data-id="${u.id}">重置密码</button>`);
            ops.push(`<button class="btn btn-small" data-op="delete" data-id="${u.id}">删除</button>`);
            return `<tr data-username="${Fmt.esc(u.username)}">` +
              `<td>${Fmt.esc(u.username)}</td>` +
              `<td>${Fmt.esc(ROLE_TEXT[u.role] || u.role)}</td>` +
              `<td>${status}</td>` +
              `<td>${Fmt.esc(u.created_at || "—")}</td>` +
              `<td>${Fmt.esc(u.last_login_at || "—")}</td>` +
              `<td>${ops.join(" ")}</td></tr>`;
          }).join("") + "</tbody></table>"
        : '<div class="empty-state"><div class="empty-icon">👥</div><div>暂无用户</div></div>';
      document.querySelectorAll("#users-table button[data-op]").forEach((b) => {
        b.addEventListener("click", () => userAction(b.dataset.op, Number(b.dataset.id),
          b.closest("tr").dataset.username));
      });
    } catch (e) { /* ignore */ }
  }

  const ACTION_CONFIRM = {
    disable: "确定停用该用户？其所有会话将立即失效。",
    enable: "确定启用该用户？",
    reset_password: "确定重置该用户密码为 123456？该用户下次登录将被强制修改密码。",
    delete: "确定删除（注销）该用户？删除后无法登录，且不可恢复。",
  };
  async function userAction(action, id, username) {
    if (!confirm(`用户「${username}」：${ACTION_CONFIRM[action] || "确定执行此操作？"}`)) return;
    try {
      const data = await API.post("/api/admin/users", { id, action });
      toast(data.message || "操作成功", "ok");
      loadUsers();
    } catch (err) {
      toast((err.data && err.data.error) || "操作失败", "");
    }
  }

  /* ── 账号安全 ── */
  async function loadAudit() {
    try {
      const d = await API.get("/api/admin/audit", 0);
      const rows = d.audit || [];
      document.getElementById("audit-table").innerHTML = rows.length
        ? `<table class="admin-table"><thead><tr><th>时间</th><th>用户</th><th>IP</th><th>动作</th></tr></thead><tbody>` +
          rows.map((r) => `<tr><td>${Fmt.esc(r.created_at)}</td><td>${Fmt.esc(r.username)}</td>` +
            `<td>${Fmt.esc(r.ip || "")}</td><td>${Fmt.esc(r.action)}</td></tr>`).join("") + "</tbody></table>"
        : '<div class="empty-state"><div class="empty-icon">🔐</div><div>暂无审计记录</div></div>';
    } catch (e) { /* ignore */ }
  }
  const pwdForm = document.getElementById("pwd-form");
  if (pwdForm) {
    const errEl = document.getElementById("pwd-error");
    pwdForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      errEl.hidden = true;
      const current = document.getElementById("pwd-current").value;
      const p1 = document.getElementById("pwd-new").value;
      const p2 = document.getElementById("pwd-confirm").value;
      if (p1 !== p2) { errEl.textContent = "两次输入的新密码不一致"; errEl.hidden = false; return; }
      if (p1.length < 8) { errEl.textContent = "新密码长度至少 8 位"; errEl.hidden = false; return; }
      try {
        const data = await API.post("/api/auth/change-password", { current, new: p1 });
        toast(data.message || "密码已修改，请重新登录", "ok");
        setTimeout(() => location.replace("/login?changed=1"), 1200);
      } catch (err) {
        errEl.textContent = (err.data && err.data.error) || "修改失败";
        errEl.hidden = false;
      }
    });
  }

  /* ── 启动 ── */
  loadOverview();
  // /admin/users 直达用户管理 tab（页面由服务端闸门校验）
  const initial = location.pathname === "/admin/users" ? "users" : location.hash.replace("#", "");
  showTab(TABS.includes(initial) ? initial : "overview");
  document.getElementById("refresh-btn").addEventListener("click", () => {
    loadOverview();
    if (document.getElementById("panel-resources").hidden === false) loadResources();
  });
  window.addEventListener("hashchange", () => {
    const t = location.hash.replace("#", "");
    if (TABS.includes(t)) showTab(t);
  });
  // 30 秒轮询（与服务端缓存同频，不会压服务器）；页面不可见时暂停
  setInterval(() => {
    if (document.hidden) return;
    loadOverview();
  }, POLL_MS);
})();
