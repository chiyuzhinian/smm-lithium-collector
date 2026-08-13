/* ── SMM 锂电价格数据中心 — 前端逻辑 ──────────────────── */

const state = {
  files: [],
  filter: 'all',
  sort: 'time-desc',
  search: '',
};

// ── 工具函数 ──────────────────────────────────────────

function formatSize(bytes) {
  if (bytes == null || bytes === 0) return '—';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
}

function formatTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function getTypeBadge(item) {
  if (item.type === 'directory') return '<span class="file-type-badge badge-folder">文件夹</span>';
  const ext = (item.name || '').split('.').pop().toLowerCase();
  const map = {
    xlsx: 'badge-xlsx', xls: 'badge-xlsx',
    csv: 'badge-csv',
    json: 'badge-json',
  };
  const cls = map[ext] || 'badge-other';
  return `<span class="file-type-badge ${cls}">${ext.toUpperCase()}</span>`;
}

function getFileIcon(item) {
  if (item.type === 'directory') return '📁';
  const ext = (item.name || '').split('.').pop().toLowerCase();
  const map = {
    xlsx: '📊', xls: '📊', csv: '📋', json: '{ }',
    pdf: '📄', txt: '📝', png: '🖼️', jpg: '🖼️', jpeg: '🖼️',
    zip: '📦', gz: '📦',
  };
  return map[ext] || '📎';
}

function getCategory(item) {
  const name = item.name || '';
  if (item.type === 'directory') {
    if (/^\d{4}$/.test(name)) return '年度数据';
    if (name.includes('固定汇总') || name.includes('汇总')) return '固定汇总';
    if (name.includes('每日汇总') || name.includes('每日')) return '每日汇总';
    return '目录';
  }
  if (name.includes('历史汇总')) return '历史汇总';
  if (name.includes('固定汇总')) return '固定汇总';
  if (name.includes('日报') || name.includes('每日')) return '每日汇总';
  const ext = (name.split('.').pop() || '').toLowerCase();
  if (ext === 'xlsx' || ext === 'xls') return 'Excel';
  if (ext === 'csv') return 'CSV';
  return '其他';
}

function getActionBtn(item) {
  if (item.type === 'directory') {
    return `<a href="${encodeURI(item.path)}" class="action-btn">📂 打开</a>`;
  }
  return `<a href="${encodeURI(item.path)}" class="action-btn" download>⬇ 下载</a>`;
}

// ── 筛选与排序 ─────────────────────────────────────────

function matchesFilter(item) {
  if (state.filter === 'all') return true;
  if (state.filter === 'excel') {
    const ext = (item.name || '').split('.').pop().toLowerCase();
    return ext === 'xlsx' || ext === 'xls';
  }
  const cat = getCategory(item);
  return cat === state.filter;
}

function matchesSearch(item) {
  if (!state.search.trim()) return true;
  const q = state.search.toLowerCase();
  return (item.name || '').toLowerCase().includes(q);
}

function sortFiles(a, b) {
  // 目录优先
  if (a.type !== b.type) return a.type === 'directory' ? -1 : 1;
  switch (state.sort) {
    case 'name-asc':  return (a.name || '').localeCompare(b.name || '', 'zh');
    case 'name-desc': return (b.name || '').localeCompare(a.name || '', 'zh');
    case 'size-desc': return (b.size || 0) - (a.size || 0);
    case 'size-asc':  return (a.size || 0) - (b.size || 0);
    case 'time-asc':  return (a.modified || '').localeCompare(b.modified || '');
    case 'time-desc':
    default:          return (b.modified || '').localeCompare(a.modified || '');
  }
}

// ── 渲染 ──────────────────────────────────────────────

function renderFileTable(filtered) {
  const tbody = document.getElementById('file-tbody');
  if (!tbody) return;
  if (filtered.length === 0) {
    tbody.innerHTML = `
      <tr><td colspan="6">
        <div class="empty-state">
          <div class="empty-icon">📭</div>
          <div>没有匹配的文件</div>
        </div>
      </td></tr>`;
    return;
  }
  tbody.innerHTML = filtered.map(f => `
    <tr>
      <td>
        <div class="file-name">
          <span class="file-icon">${getFileIcon(f)}</span>
          <a href="${encodeURI(f.path)}">${escHtml(f.name)}</a>
        </div>
      </td>
      <td>${getTypeBadge(f)}</td>
      <td class="file-size">${f.type === 'directory' ? f.children_count + ' 项' : formatSize(f.size)}</td>
      <td class="file-time">${formatTime(f.modified)}</td>
      <td class="file-cat">${getCategory(f)}</td>
      <td>${getActionBtn(f)}</td>
    </tr>
  `).join('');
}

function renderRecentFiles(files) {
  const container = document.getElementById('recent-list');
  if (!container) return;
  const recent = files
    .filter(f => f.type === 'file')
    .sort((a, b) => (b.modified || '').localeCompare(a.modified || ''))
    .slice(0, 10);
  if (recent.length === 0) {
    container.innerHTML = '<div class="empty-state"><div class="empty-icon">📄</div><div>暂无文件</div></div>';
    return;
  }
  container.innerHTML = recent.map(f => `
    <div class="recent-item">
      <span class="recent-icon">${getFileIcon(f)}</span>
      <div class="recent-info">
        <div class="recent-name">${escHtml(f.name)}</div>
        <div class="recent-meta">
          <span>${formatSize(f.size)}</span>
          <span>${formatTime(f.modified)}</span>
          <span>${getCategory(f)}</span>
        </div>
      </div>
      <a href="${encodeURI(f.path)}" class="btn btn-outline recent-dl" download>⬇ 下载</a>
    </div>
  `).join('');
}

function updateStats(files) {
  const fileCount = files.filter(f => f.type === 'file').length;
  const dirCount = files.filter(f => f.type === 'directory').length;
  const latest = files.reduce((max, f) => {
    const m = f.modified || '';
    return m > max ? m : max;
  }, '');
  const years = new Set();
  files.forEach(f => {
    const m = f.modified || '';
    const y = m.slice(0, 4);
    if (y) years.add(y);
  });

  setEl('stat-files', fileCount.toString());
  setEl('stat-dirs', dirCount.toString());
  setEl('stat-update', latest ? latest.slice(0, 10) : '—');
  setEl('stat-years', years.size > 0 ? [...years].sort().join('/') : '—');
  setEl('footer-time', latest ? formatTime(latest) : '—');
}

function setEl(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function escHtml(s) {
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(s).replace(/[&<>"']/g, (c) => map[c]);
}

// ── 主刷新 ────────────────────────────────────────────

function refresh() {
  const filtered = state.files
    .filter(matchesFilter)
    .filter(matchesSearch)
    .sort(sortFiles);
  renderFileTable(filtered);
  renderRecentFiles(state.files);
  updateStats(state.files);
  document.getElementById('result-count').textContent = `共 ${filtered.length} 项`;
}

// ── 事件绑定 ──────────────────────────────────────────

function initFilters() {
  document.querySelectorAll('.filter-tag').forEach(tag => {
    tag.addEventListener('click', () => {
      document.querySelectorAll('.filter-tag').forEach(t => t.classList.remove('active'));
      tag.classList.add('active');
      state.filter = tag.dataset.filter;
      refresh();
    });
  });
}

function initSearch() {
  const input = document.getElementById('search-input');
  if (!input) return;
  input.addEventListener('input', () => {
    state.search = input.value;
    refresh();
  });
}

function initSort() {
  const sel = document.getElementById('sort-select');
  if (!sel) return;
  sel.addEventListener('change', () => {
    state.sort = sel.value;
    refresh();
  });
}

// ── 表格列头排序 ──────────────────────────────────────

function initTableSort() {
  document.querySelectorAll('.file-table th[data-sort]').forEach(th => {
    th.addEventListener('click', () => {
      const newSort = th.dataset.sort;
      if (state.sort === newSort + '-desc') state.sort = newSort + '-asc';
      else state.sort = newSort + '-desc';
      document.getElementById('sort-select').value = state.sort;
      refresh();
    });
  });
}

// ── 初始化 ────────────────────────────────────────────

async function init() {
  try {
    const resp = await fetch('/api/files');
    if (!resp.ok) throw new Error('API 返回 ' + resp.status);
    state.files = await resp.json();
  } catch (err) {
    console.error('加载文件列表失败:', err);
    state.files = [];
    const tbody = document.getElementById('file-tbody');
    if (tbody) tbody.innerHTML = `<tr><td colspan="6">
      <div class="empty-state"><div class="empty-icon">⚠️</div><div>无法加载文件列表：${escHtml(err.message)}</div></div>
    </td></tr>`;
  }

  initFilters();
  initSearch();
  initSort();
  initTableSort();
  refresh();
}

document.addEventListener('DOMContentLoaded', init);
