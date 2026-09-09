#!/usr/bin/env python
"""V4 回归修复验收：趋势页/报表双Tab/数据质量/运维中心 + Excel 下载校验 + 截图。

用法: .venv/bin/python scripts/verify_regression.py [--shoot]
"""
import io
import json
import re
import sys
from pathlib import Path

from openpyxl import load_workbook
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8899"
OUT = Path("data/screenshots/v4")
SHOOT = "--shoot" in sys.argv
VIEWPORTS = [(1440, 900), (1920, 1080), (1366, 768), (375, 812)]
USER, PWD = "admin", "DevPass2026"

FAILS = []
NOTES = []


def check(cond, label, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


def js(page, code):
    return page.evaluate(code)


def no_h_overflow(page):
    return js(page, "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


def rgb2hex(s):
    m = re.match(r"rgba?\((\d+),(\d+),(\d+)", (s or "").replace(" ", ""))
    return "#%02X%02X%02X" % tuple(int(x) for x in m.groups()) if m else None


def bg(page, sel):
    return rgb2hex(js(page, f"getComputedStyle(document.querySelector({sel!r})).backgroundColor"))


# 对比度（WCAG）
CONTRAST = """
(h1, h2) => {
  const lum = (h) => {
    const c = h.replace('#','');
    const [r,g,b] = [0,2,4].map(i => parseInt(c.slice(i,i+2),16)/255)
      .map(v => v <= 0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4));
    return 0.2126*r + 0.7152*g + 0.0722*b;
  };
  const [l1,l2] = [lum(h1), lum(h2)].sort((a,b)=>b-a);
  return (l1+0.05)/(l2+0.05);
}
"""


def find_dark_blocks(page, root_sel):
    """返回 main 区域内背景亮度 < 0.15 的可疑元素（深色残留，V5 浅色主题下不应出现）。"""
    return js(page, f"""
      (() => {{
        const out = [];
        document.querySelectorAll({root_sel!r} + ' *').forEach((el) => {{
          const s = getComputedStyle(el);
          if (s.display === 'none' || s.visibility === 'hidden') return;
          const m = s.backgroundColor.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)(?:,\\s*([\\d.]+))?/);
          if (!m) return;
          const alpha = m[4] === undefined ? 1 : +m[4];
          if (alpha < 0.05) return;   // 透明背景不算深色残留
          const [r, g, b] = [+m[1], +m[2], +m[3]];
          const lum = (0.2126*r + 0.7152*g + 0.0722*b) / 255;
          if (lum < 0.15) {{
            const rect = el.getBoundingClientRect();
            if (rect.width > 40 && rect.height > 20)
              out.push({{ tag: el.tagName, cls: String(el.className).slice(0, 40), bg: s.backgroundColor }});
          }}
        }});
        return out.slice(0, 8);
      }})()
    """)


def focusable_in_hidden(page, sel):
    """隐藏容器内仍可聚焦的元素数量（应全为 0）。"""
    return js(page, f"""
      (() => {{
        let n = 0;
        document.querySelectorAll({sel!r} + ' input, ' + {sel!r} + ' button, ' + {sel!r} + ' select, ' + {sel!r} + ' a').forEach((el) => {{
          if (el.offsetParent !== null) n++;
        }});
        return n;
      }})()
    """)


# ECharts 日期标签（沿用 V3 验证过的实现：tspan 文本+宽度，convertToPixel 标量锚点）
CHART_LABELS = """
(() => {
  const chart = echarts.getInstanceByDom(document.getElementById('chart'));
  if (!chart) return [];
  const W = chart.getWidth();
  const data = chart.getOption().xAxis[0].data || [];
  const out = [];
  for (const el of chart.getZr().storage.getDisplayList()) {
    if ((el.type !== 'text' && el.type !== 'tspan') || !el.style || !el.style.text) continue;
    const t = el.style.text;
    if (!/^(\\d{4}-)?\\d{2}-\\d{2}$/.test(t)) continue;
    const full = data.find((d) => d === t) || data.find((d) => d.endsWith("-" + t));
    if (full === undefined) continue;
    const anchor = chart.convertToPixel({ xAxisIndex: 0 }, full);
    const w = el.getBoundingRect().width;
    out.push({ x: +(anchor - w / 2).toFixed(1), x2: +(anchor + w / 2).toFixed(1), t, W });
  }
  out.sort((a, b) => a.x - b.x);
  return out;
})()
"""


def wait_ready(page, name):
    if name == "trends":
        page.wait_for_selector("#chart canvas", timeout=45000)
        page.wait_for_timeout(1500)
    elif name == "reports":
        page.wait_for_selector("#tab-dataset .data-table", timeout=30000)
    elif name == "quality":
        page.wait_for_selector("#quality-tbody tr", timeout=30000)
    elif name == "admin":
        page.wait_for_selector(".admin-nav a", timeout=30000)
        page.wait_for_timeout(1200)


# ══════════ 趋势页 ══════════

def verify_trends(page, w, h, tag):
    page.goto(BASE + "/trends")
    wait_ready(page, "trends")
    P = f"[{tag}] 趋势"

    check(not js(page, "!!document.getElementById('detail-scroll')"), f"{P} 无底部明细表残留")
    check(not js(page, "!!document.getElementById('detail-loading')"), f"{P} 无明细空容器残留")
    check(no_h_overflow(page), f"{P} 无横向溢出")

    # V5：默认空选择 → 引导文案（ECharts title 渲染在 canvas，读 option 而非 textContent）
    empty_txt = js(page, """(() => {
      const c = echarts.getInstanceByDom(document.getElementById('chart'));
      if (!c) return '';
      const t = c.getOption().title;
      return (t && t[0] && t[0].text) || '';
    })()""")
    check("添加产品" in (empty_txt or ""), f"{P} 默认空选择显示引导", (empty_txt or "")[:50])
    page.fill("#product-pick", "碳酸锂")
    page.wait_for_timeout(400)
    page.evaluate("() => { const b = document.querySelector('#product-suggest .suggest-item'); if (b) b.click(); }")
    page.wait_for_timeout(1200)

    ch = js(page, "document.getElementById('chart').getBoundingClientRect().height")
    if w >= 860:
        check(360 <= ch <= 500, f"{P} 图表高度 360-500px", f"{ch:.0f}px")

    # 摘要行（单品种）
    sum_text = js(page, "document.getElementById('chart-summary').textContent")
    check("期末日均价" in sum_text and "区间变化" in sum_text and "有效报价点" in sum_text,
          f"{P} 单品种紧凑摘要行", sum_text[:70])
    check("→" in sum_text, f"{P} 区间变化带实际日期")

    # 日期标签完整
    labels = js(page, CHART_LABELS)
    if len(labels) >= 2:
        canvas_w = labels[0]["W"]
        ok_bounds = min(l["x"] for l in labels) >= -1 and max(l["x2"] for l in labels) <= canvas_w - 1
        check(ok_bounds, f"{P} 首尾日期标签完整",
              f"first={labels[0]['x']} last_x2={labels[-1]['x2']} W={canvas_w}")
        check(labels[0]["t"] != labels[-1]["t"], f"{P} 日期轴多刻度")

    # custom-range 显隐
    cr_hidden = js(page, "document.getElementById('custom-range').hidden")
    check(cr_hidden is True, f"{P} 自定义日期默认隐藏")
    page.evaluate("""() => document.querySelector('#range-seg button[data-range="custom"]').click()""")
    page.wait_for_timeout(400)
    check(js(page, "!document.getElementById('custom-range').hidden"), f"{P} 切自定义后日期可见")
    page.evaluate("""() => document.querySelector('#range-seg button[data-range="7d"]').click()""")
    page.wait_for_timeout(800)

    # Tooltip
    tip_ok = js(page, """
      (() => {
        const chart = echarts.getInstanceByDom(document.getElementById('chart'));
        const n = chart.getOption().xAxis[0].data.length;
        chart.dispatchAction({ type: 'showTip', seriesIndex: 2, dataIndex: n - 1 });
        const div = document.querySelector('#chart div[style*="position: absolute"]');
        if (!div) return { ok: false };
        const r = div.getBoundingClientRect();
        const c = document.getElementById('chart').getBoundingClientRect();
        return { ok: true, text: div.textContent, inside: r.left >= c.left - 1 && r.right <= c.right + 1 && r.bottom <= c.bottom + 1 };
      })()
    """)
    check(tip_ok["ok"] and "报价日期" in tip_ok["text"] and "日均价" in tip_ok["text"],
          f"{P} Tooltip 完整且不越界", (tip_ok.get("text") or "")[:60])

    # 多品种 → 摘要行清空（同单位单轴）
    page.fill("#product-pick", "磷酸铁锂")
    page.wait_for_timeout(400)
    page.evaluate("() => { const b = document.querySelector('#product-suggest .suggest-item'); if (b) b.click(); }")
    page.wait_for_timeout(900)
    chips = js(page, "document.querySelectorAll('#chips-row .legend-chip').length")
    sum2 = js(page, "document.getElementById('chart-summary').textContent")
    check(chips == 2 and str(sum2).strip() == "", f"{P} 多品种仅图例无综合摘要", f"{chips} chips")

    # V5：跨单位自动双 Y 轴（加入 元/Wh 电芯）
    page.fill("#product-pick", "100Ah")
    page.wait_for_timeout(400)
    page.evaluate("() => { const b = document.querySelector('#product-suggest .suggest-item'); if (b) b.click(); }")
    page.wait_for_timeout(900)
    yaxes = js(page, "echarts.getInstanceByDom(document.getElementById('chart')).getOption().yAxis")
    check(isinstance(yaxes, list) and len(yaxes) == 2, f"{P} 跨单位双 Y 轴",
          f"yAxis 数={len(yaxes) if isinstance(yaxes, list) else 1}")
    # 移除电芯恢复双产品，继续 resize 检查
    page.evaluate("() => { const b = document.querySelector('#chips-row .legend-remove'); if (b) b.click(); }")
    page.wait_for_timeout(900)

    # 图表随容器 resize（content-max=1400：视口 >1400 时容器已封顶，跳过）
    if w <= 1400:
        w0 = js(page, "echarts.getInstanceByDom(document.getElementById('chart')).getWidth()")
        page.set_viewport_size({"width": max(w - 300, 800), "height": h})
        page.wait_for_timeout(700)
        w1 = js(page, "echarts.getInstanceByDom(document.getElementById('chart')).getWidth()")
        check(abs(w1 - w0) > 150, f"{P} 图表随容器尺寸更新", f"{w0} → {w1}")
        page.set_viewport_size({"width": w, "height": h})
        page.wait_for_timeout(600)

    if SHOOT:
        page.screenshot(path=str(OUT / f"trends-{tag}.png"))


# ══════════ 数据与报表 ══════════

def verify_reports(page, w, h, tag):
    page.goto(BASE + "/reports")
    wait_ready(page, "reports")
    P = f"[{tag}] 报表"

    # Tab 互斥（dataset 激活时月报完全退出）
    m_sec = js(page, """
      (() => { const el = document.getElementById('tab-monthly');
        return { hidden: el.hidden, h: el.offsetHeight, display: getComputedStyle(el).display }; })()
    """)
    check(m_sec["hidden"] and m_sec["h"] == 0, f"{P} 月报区不显示不占位", m_sec)
    check(focusable_in_hidden(page, "#tab-monthly") == 0, f"{P} 月报区不进键盘焦点")
    check(no_h_overflow(page), f"{P} 无横向溢出")

    # 首屏 ≥8 行（1440×900）
    rows_visible = js(page, """
      (() => {
        const vh = window.innerHeight;
        let n = 0;
        document.querySelectorAll('#ds-scroll tbody tr').forEach(tr => {
          const r = tr.getBoundingClientRect();
          if (r.top >= 0 && r.bottom <= vh + 1) n++;
        });
        return n;
      })()
    """)
    if w >= 1100:
        check(rows_visible >= 8, f"{P} 分类数据首屏 ≥8 行", f"{rows_visible} 行")

    # V5：分类多选（下拉 + chips 累积；两个分类同时查）
    cat_w = js(page, "document.getElementById('ds-cat-pick').getBoundingClientRect().width")
    check(cat_w <= 200, f"{P} 分类框宽度克制", f"{cat_w:.0f}px")
    page.evaluate("() => document.getElementById('ds-cat-pick').click()")
    page.wait_for_timeout(400)
    page.evaluate("""() => {
      const items = [...document.querySelectorAll('#ds-cat-suggest .suggest-item')];
      if (items[0]) items[0].click();
    }""")
    page.wait_for_timeout(700)
    page.evaluate("() => document.getElementById('ds-cat-pick').click()")
    page.wait_for_timeout(400)
    page.evaluate("""() => {
      const items = [...document.querySelectorAll('#ds-cat-suggest .suggest-item')];
      if (items[1]) items[1].click();
    }""")
    page.wait_for_timeout(900)
    chips_n = js(page, "document.querySelectorAll('#ds-cat-chips .legend-chip').length")
    check(chips_n == 2, f"{P} 分类多选 chips 累积", f"{chips_n} chips")
    # 多分类结果行受限在所选分类内
    cats_in_view = js(page, """
      [...new Set([...document.querySelectorAll('#ds-scroll tbody td.ds-cat')].map(td => td.textContent.trim()))]
    """)
    check(len(cats_in_view) <= 2, f"{P} 多分类过滤生效", str(cats_in_view))

    # 分页可访问、不遮挡最后一行
    pager_ok = js(page, """
      (() => {
        const pager = document.querySelector('.pager-bar');
        const p = pager.getBoundingClientRect();
        const doc = document.documentElement;
        window.scrollTo(0, doc.scrollHeight);
        const p2 = pager.getBoundingClientRect();
        const rows = [...document.querySelectorAll('#ds-scroll tbody tr')];
        const last = rows[rows.length - 1].getBoundingClientRect();
        return { inDoc: p2.top < window.innerHeight, lastAbovePager: last.bottom <= p2.top + 1 };
      })()
    """)
    check(pager_ok["inDoc"], f"{P} 分页随页面可达")
    page.evaluate("() => window.scrollTo(0, 0)")

    # 切月报 → 分类表格完全退出
    page.evaluate("() => document.querySelector('#tab-seg button[data-tab=monthly]').click()")
    page.wait_for_timeout(600)
    d_sec = js(page, """
      (() => { const el = document.getElementById('tab-dataset');
        return { hidden: el.hidden, h: el.offsetHeight }; })()
    """)
    check(d_sec["hidden"] and d_sec["h"] == 0, f"{P} 切月报后分类区完全退出", d_sec)
    check(focusable_in_hidden(page, "#tab-dataset") == 0, f"{P} 分类区不进键盘焦点")
    check(js(page, "!!document.querySelector('#tab-monthly #month-input')"), f"{P} 月报工具栏可见")

    # 月报五态：加载中 → 成功
    page.wait_for_selector("#month-scroll .data-table", timeout=25000)
    page.wait_for_timeout(500)
    m_rows = js(page, "document.querySelectorAll('#month-scroll tbody tr').length")
    check(m_rows >= 40, f"{P} 月报预览行数", f"{m_rows} 行")
    check(js(page, "document.getElementById('month-status').hidden === false"), f"{P} 月报汇总条显示")
    check(js(page, "document.getElementById('method-panel').tagName === 'DETAILS'"), f"{P} 计算说明可折叠")

    # 切回 dataset：状态保留
    page.evaluate("() => document.querySelector('#tab-seg button[data-tab=dataset]').click()")
    page.wait_for_timeout(600)
    check(js(page, "document.querySelectorAll('#ds-scroll tbody tr').length > 0"), f"{P} 切回分类数据正常")

    if SHOOT:
        page.screenshot(path=str(OUT / f"reports-{tag}.png"))


# ══════════ 数据质量 ══════════

def verify_quality(page, w, h, tag):
    page.goto(BASE + "/quality")
    wait_ready(page, "quality")
    P = f"[{tag}] 质量"

    check(not js(page, "[...document.styleSheets].some(s => (s.href||'').includes('style.css'))"),
          f"{P} 不再加载旧 style.css")
    check(no_h_overflow(page), f"{P} 无横向溢出")

    darks = find_dark_blocks(page, ".main-container")
    check(len(darks) == 0, f"{P} 无深色残留区块", darks)

    title_contrast = js(page, """
      (() => {
        const lum = (cs) => {
          const [r, g, b] = cs.match(/\\d+/g).slice(0, 3).map(Number);
          const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
          return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
        };
        const c1 = getComputedStyle(document.querySelector('.section-title')).color;
        const c2 = getComputedStyle(document.body).backgroundColor;
        const [l1, l2] = [lum(c1), lum(c2)].sort((a, b) => b - a);
        return (l1 + 0.05) / (l2 + 0.05);
      })()
    """)
    check(title_contrast >= 4.5, f"{P} 标题对比度 ≥4.5", f"{title_contrast:.2f}")

    # 内容真实渲染
    stats = js(page, "document.querySelectorAll('#quality-today .q-stat').length")
    rows = js(page, "document.querySelectorAll('#quality-tbody tr').length")
    gaps = js(page, "document.querySelectorAll('#gaps-list .gap-item').length + (document.querySelector('#gaps-list .quality-state') ? 1 : 0)")
    check(stats >= 6, f"{P} 质量状态统计卡渲染", f"{stats} 项")
    check(rows >= 1, f"{P} 近14日明细渲染", f"{rows} 行")
    check(gaps >= 1, f"{P} 缺口清单渲染或明确空态", f"{gaps} 项")

    # 阅读顺序
    order = js(page, """
      [...document.querySelectorAll('.section-title')].map(e => e.textContent.trim())
    """)
    check(order[0] == "当前质量状态" and "固定汇总" in order[1] and "14 日" in order[2] and "缺口" in order[3],
          f"{P} 阅读顺序", order)

    # 状态徽标对比度（warn 徽标 vs 面板底）
    badge_ok = js(page, """
      (() => {
        const b = document.querySelector('.status-badge.warn');
        if (!b) return null;
        return { color: getComputedStyle(b).color, bg: getComputedStyle(b).backgroundColor };
      })()
    """)
    if badge_ok:
        bc = js(page, """
          (() => {
            // 合成 alpha：徽标文字色 vs 徽标背景（含透明）叠在面板色上的实际背景
            const lum = (r, g, b) => {
              const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
              return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
            };
            const b = document.querySelector('.status-badge.warn');
            const fg = getComputedStyle(b).color.match(/\\d+/g).slice(0, 3).map(Number);
            const bgm = getComputedStyle(b).backgroundColor.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)(?:,\\s*([\\d.]+))?\\)/);
            let br, bg2, bb;
            const panel = getComputedStyle(document.body).backgroundColor.match(/\\d+/g).slice(0, 3).map(Number);
            if (bgm && bgm[4] !== undefined) {
              const a = parseFloat(bgm[4]);
              br = Math.round(+bgm[1] * a + panel[0] * (1 - a));
              bg2 = Math.round(+bgm[2] * a + panel[1] * (1 - a));
              bb = Math.round(+bgm[3] * a + panel[2] * (1 - a));
            } else { [br, bg2, bb] = [+bgm[1], +bgm[2], +bgm[3]]; }
            const [l1, l2] = [lum(...fg), lum(br, bg2, bb)].sort((a, b) => b - a);
            return (l1 + 0.05) / (l2 + 0.05);
          })()
        """)
        check(bc >= 4.5, f"{P} 徽标文字对比度 ≥4.5", f"{bc:.2f}")

    if SHOOT:
        page.screenshot(path=str(OUT / f"quality-{tag}.png"))


# ══════════ 运维中心 ══════════

def verify_admin(page, w, h, tag):
    page.goto(BASE + "/admin")
    wait_ready(page, "admin")
    P = f"[{tag}] 运维"

    check(not js(page, "[...document.styleSheets].some(s => (s.href||'').includes('style.css'))"),
          f"{P} 不再加载旧 style.css")
    check(no_h_overflow(page), f"{P} 无横向溢出")
    check(bg(page, ".admin-sidebar") == "#FFFFFF", f"{P} 侧栏浅色", bg(page, ".admin-sidebar"))
    darks = find_dark_blocks(page, ".admin-main")
    check(len(darks) == 0, f"{P} 无深色卡片残留", darks)

    # 侧栏 SVG 图标、无 emoji
    svg_count = js(page, "document.querySelectorAll('.admin-nav .svg-icon').length")
    emoji = js(page, "[...document.querySelectorAll('.admin-nav a')].filter(a => /[\\u{1F300}-\\u{1FAFF}]/u.test(a.textContent)).length")
    check(svg_count == 7, f"{P} 侧栏 SVG 图标", f"{svg_count} 个")
    check(emoji == 0, f"{P} 侧栏无 emoji")

    # 告警合并：默认 ≤3 行可见 + 无重复 PACK；可展开
    a = js(page, """
      (() => {
        const rows = [...document.querySelectorAll('#alerts-box .alert-row')].filter(r => r.offsetParent !== null);
        const texts = rows.map(r => r.textContent);
        const pack = texts.filter(t => t.includes('PACK')).length;
        const toggle = document.getElementById('alerts-toggle');
        return { visible: rows.length, pack, hasToggle: !!toggle,
                 totalHint: toggle ? toggle.textContent : '' };
      })()
    """)
    check(a["visible"] <= 3, f"{P} 告警默认少量展示", f"{a['visible']} 行")
    check(a["pack"] <= 1, f"{P} PACK 告警合并无重复", f"{a['pack']} 处")
    if a["hasToggle"]:
        page.evaluate("() => document.getElementById('alerts-toggle').click()")
        page.wait_for_timeout(300)
        check(js(page, "!document.getElementById('alerts-more').hidden"), f"{P} 查看全部 N 条展开")
        page.evaluate("() => document.getElementById('alerts-toggle').click()")

    # 服务状态紧凑表
    svc = js(page, "document.querySelectorAll('#status-table-box tbody tr').length")
    check(svc == 5, f"{P} 服务状态表 5 行", f"{svc} 行")

    # 资源面板：进度条 + 真实数据
    res = js(page, """
      (() => {
        const bars = document.querySelectorAll('#resources-inline .res-bar-fill');
        const diskText = document.querySelector('#resources-inline').textContent;
        return { bars: bars.length, hasGB: /GB/.test(diskText), hasUnknownZero: /0%/.test(diskText) };
      })()
    """)
    check(res["bars"] >= 3, f"{P} 资源进度条", f"{res['bars']} 条")
    check(res["hasGB"], f"{P} 资源真实单位")

    # Tab 切换 + 隐藏面板不进焦点
    page.evaluate("() => document.querySelector('.admin-nav a[data-tab=users]').click()")
    page.wait_for_timeout(800)
    check(js(page, "!document.getElementById('panel-users').hidden"), f"{P} 子页切换（用户管理）")
    hidden_focus = focusable_in_hidden(page, "#panel-overview")
    check(hidden_focus == 0, f"{P} 隐藏面板不进焦点", hidden_focus)
    page.evaluate("() => document.querySelector('.admin-nav a[data-tab=overview]').click()")
    page.wait_for_timeout(500)

    if SHOOT:
        page.screenshot(path=str(OUT / f"admin-{tag}.png"))


# ══════════ 主流程 ══════════

def main():
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
        page = ctx.new_page()

        def on_console(m):
            if m.type == "error" and not ("401" in m.text or "Failed to load resource" in m.text):
                errors.append(m.text)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", on_console)

        page.goto(BASE + "/login")
        page.fill("#username", USER)
        page.fill("#password", PWD)
        page.click("#login-btn", force=True, timeout=15000)
        page.wait_for_url("**/", timeout=15000)

        for w, h in VIEWPORTS:
            page.set_viewport_size({"width": w, "height": h})
            tag = f"{w}x{h}"
            verify_trends(page, w, h, tag)
            verify_reports(page, w, h, tag)
            verify_quality(page, w, h, tag)
            verify_admin(page, w, h, tag)

        # 125% 缩放
        page.set_viewport_size({"width": 1152, "height": 720})
        page.evaluate("() => { document.body.style.zoom = 1.25; window.dispatchEvent(new Event('resize')); }")
        page.wait_for_timeout(800)
        for name, path in [("trends", "/trends"), ("reports", "/reports"), ("quality", "/quality"), ("admin", "/admin")]:
            page.goto(BASE + path)
            wait_ready(page, name)
            check(no_h_overflow(page), f"[z125] {name} 无横向溢出",
                  f"sw={js(page,'document.documentElement.scrollWidth')} cw={js(page,'document.documentElement.clientWidth')}")
            if SHOOT:
                page.screenshot(path=str(OUT / f"{name}-z125.png"))
        page.evaluate("() => { document.body.style.zoom = 1; }")

        # 月报 Excel 下载校验
        try:
            resp = page.request.get(f"{BASE}/api/portal/monthly/download?month=2026-08")
            if resp.status == 200:
                data = resp.body()
                wb = load_workbook(io.BytesIO(data))
                sheets = wb.sheetnames
                ws = wb[sheets[0]]
                headers = [c.value for c in ws[1]]
                check(len(sheets) >= 2, "月报 Excel 工作表数", sheets)
                check(ws.max_row >= 49, "月报 Excel 业务行 ≥49", f"{ws.max_row} 行")
                check(any(("月均价" in str(x) or "均价" in str(x)) for x in headers) and any(("环比" in str(x) or "涨跌" in str(x)) for x in headers),
                      "月报 Excel 关键字段", headers[:9])
                first_data_col = [c.value for c in ws[2]]
                check(any("2026" in str(x) or "月" in str(x) for x in first_data_col), "月报 Excel 业务内容")
                # 组织列（第1列）应为中性板块名（V5 改名）
                orgs = {ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)}
                NEUTRAL_ORGS = {"回收循环板块", "正极材料板块", "电芯电池板块", "三元材料板块"}
                check(any(o in NEUTRAL_ORGS for o in orgs) and len(orgs) >= 3
                      and not any("华友" in str(o) for o in orgs),
                      "月报 Excel 组织合并列（中性板块名）", f"{len(orgs)} 个组织 {sorted(str(o) for o in orgs)[:4]}")
            else:
                check(False, "月报 Excel 下载", f"HTTP {resp.status}")
        except Exception as e:
            check(False, "月报 Excel 下载校验", str(e)[:120])

        print("\n--- JS 错误（已白名单 401 噪声） ---")
        for e in errors:
            print(" !", e)
        check(not errors, "无新增 JS 错误")

        browser.close()

    print(f"\n=== 结果: {len(FAILS)} 项失败 ===")
    for f in FAILS:
        print(" FAIL:", f)
    if NOTES:
        print(" 备注:")
        for n in NOTES:
            print("  -", n)
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
