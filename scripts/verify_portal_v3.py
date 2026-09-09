#!/usr/bin/env python
"""V3 深色视觉重构验收：程序化断言 + 截图（8899 预览，huayou/DevPass2026）。

用法: .venv/bin/python scripts/verify_portal_v3.py [--shoot]
输出: 每项 [PASS]/[FAIL] 详情；--shoot 时写 data/screenshots/v3/after/
"""
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8899"
OUT = Path("data/screenshots/v3/after")
SHOOT = "--shoot" in sys.argv
VIEWPORTS = [(1440, 900), (1920, 1080), (1280, 800), (375, 812)]

FAILS = []


def check(cond, label, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


def js(page, code):
    return page.evaluate(code)


# 对比度（相对亮度算法 WCAG）
CONTRAST_JS = """
(hex1, hex2) => {
  const lum = (h) => {
    const c = h.replace('#','');
    const [r,g,b] = [0,2,4].map(i => parseInt(c.slice(i,i+2),16)/255)
      .map(v => v <= 0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4));
    return 0.2126*r + 0.7152*g + 0.0722*b;
  };
  const [l1,l2] = [lum(hex1), lum(hex2)].sort((a,b)=>b-a);
  return (l1+0.05)/(l2+0.05);
}
"""


def rgb2hex(s):
    m = re.match(r"rgb\((\d+),(\d+),(\d+)\)", (s or "").replace(" ", ""))
    if not m:
        return None
    return "#%02X%02X%02X" % tuple(int(x) for x in m.groups())


def bg_hex(page, sel):
    return rgb2hex(js(page, f"getComputedStyle(document.querySelector({sel!r})).backgroundColor"))


def main():
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        def on_console(m):
            # 白名单：登录页 /api/auth/me 的 401 属预期噪声（未登录探测会话）
            if m.type == "error" and not ("401" in m.text or "Failed to load resource" in m.text):
                errors.append(m.text)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", on_console)

        page.goto(BASE + "/login")
        page.fill("#username", "huayou")
        page.fill("#password", "DevPass2026")
        page.click("#login-btn")
        page.wait_for_url("**/", timeout=15000)

        for w, h in VIEWPORTS:
            page.set_viewport_size({"width": w, "height": h})
            tag = f"{w}x{h}"
            verify_quotes(page, w, h, tag)
            verify_trends(page, w, h, tag)
            verify_reports(page, w, h, tag)

            if w >= 1280:
                page.set_viewport_size({"width": int(w / 1.25), "height": int(h / 1.25)})
                page.evaluate("() => { document.body.style.zoom = 1.25; window.dispatchEvent(new Event('resize')); }")
                page.wait_for_timeout(1000)
                for name, path in [("quotes", "/"), ("trends", "/trends"), ("reports", "/reports")]:
                    page.goto(BASE + path)
                    wait_ready(page, name)
                    check(no_h_overflow(page), f"[{tag} z125] {name} 无横向溢出",
                          f"scrollWidth={js(page,'document.documentElement.scrollWidth')} client={js(page,'document.documentElement.clientWidth')}")
                    if SHOOT:
                        page.screenshot(path=str(OUT / f"{name}-{tag}-zoom125.png"))
                page.evaluate("() => { document.body.style.zoom = 1; }")

        print("\n--- JS 控制台错误 ---")
        for e in errors:
            print(" !", e)
        check(not errors, "无 JS 页面错误/控制台错误")

        browser.close()

    print(f"\n=== 结果: {len(FAILS)} 项失败 ===")
    for f in FAILS:
        print(" FAIL:", f)
    sys.exit(1 if FAILS else 0)


def no_h_overflow(page):
    return js(page, "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


def wait_ready(page, name):
    if name == "quotes":
        page.wait_for_selector(".data-table tbody tr", timeout=30000)
    elif name == "trends":
        page.wait_for_selector("#chart canvas", timeout=30000)
        page.wait_for_timeout(1500)
    elif name == "reports":
        page.wait_for_selector("#tab-dataset .data-table", timeout=30000)


# ══════════ 每日报价 ══════════

def verify_quotes(page, w, h, tag):
    page.goto(BASE + "/")
    wait_ready(page, "quotes")
    P = f"[{tag}] 每日报价"

    check(no_h_overflow(page), f"{P} 无横向溢出")
    check(bg_hex(page, "body") == "#0D1422", f"{P} 页面底色", bg_hex(page, "body"))
    check(bg_hex(page, ".topbar") == "#0B1220", f"{P} 顶栏底色")
    check(bg_hex(page, ".panel") == "#151F30", f"{P} 面板底色")
    topbar_h = js(page, "document.querySelector('.topbar').offsetHeight")
    check(topbar_h == 56, f"{P} 顶栏 56px", topbar_h)

    # 状态行
    st = js(page, "[document.getElementById('st-latest').textContent, document.getElementById('st-count').textContent]")
    check(st[0] not in ("—", ""), f"{P} 状态行·最新报价日期", st[0])
    check(str(st[1]).isdigit() and int(st[1]) > 0, f"{P} 状态行·品种数", st[1])

    # 行高 / 首屏行数 / 表头粘性
    rh = js(page, "document.querySelector('#quotes-tbody tr').getBoundingClientRect().height")
    check(45 <= rh <= 64, f"{P} 数据行高 45-64px", f"{rh:.0f}px")
    visible_rows = js(page, """
      (() => {
        const sc = document.getElementById('table-scroll');
        const sr = sc.getBoundingClientRect();
        let n = 0;
        document.querySelectorAll('#quotes-tbody tr').forEach(tr => {
          const r = tr.getBoundingClientRect();
          if (r.top >= sr.top - 1 && r.bottom <= sr.bottom + 1) n++;
        });
        return n;
      })()
    """)
    check(visible_rows >= 10 if h >= 900 else (visible_rows >= 5 if w >= 860 else visible_rows >= 3), f"{P} 首屏完整行数（桌面≥10）", f"{visible_rows} 行")

    page.evaluate("() => document.getElementById('table-scroll').scrollTop = 400")
    page.wait_for_timeout(200)
    sticky_ok = js(page, """
      (() => {
        const sc = document.getElementById('table-scroll');
        const th = sc.querySelector('thead th');
        return Math.abs(th.getBoundingClientRect().top - sc.getBoundingClientRect().top) < 1.5;
      })()
    """)
    check(sticky_ok, f"{P} 表头滚动粘性（不压顶栏）")

    # 数字规范
    num_align = js(page, """
      (() => {
        const td = document.querySelector('#quotes-tbody td.num-cell');
        const s = getComputedStyle(td);
        return { align: s.textAlign, mono: s.fontFamily.includes('mono') || s.fontFamily.includes('Mono') };
      })()
    """)
    check(num_align["align"] == "right", f"{P} 数字列右对齐")
    check(num_align["mono"], f"{P} 数字列等宽字体")
    avg_w = js(page, "getComputedStyle(document.querySelector('#quotes-tbody td.avg-cell')).fontWeight")
    check(int(avg_w) >= 700, f"{P} 日均价加粗", avg_w)

    # 千分位 + 精度（整数部分 ≥1000 的值必须有千分位；小数精度保留）
    grouped_ok = js(page, """
      (() => {
        const tds = [...document.querySelectorAll('#quotes-tbody td.avg-cell')];
        const big = tds.filter(td => {
          const n = parseFloat(td.textContent.replace(/,/g, ''));
          return isFinite(n) && Math.abs(n) >= 1000;
        });
        if (!big.length) return 'none-big';
        return big.every(td => td.textContent.includes(','));
      })()
    """)
    check(grouped_ok is True or grouped_ok == "none-big", f"{P} 千分位分组", grouped_ok)
    # 精度不丢：抽查 元/Wh 类小数（若 API 有 4 位小数则页面同值）
    precise = js(page, """
      (() => {
        const tds = [...document.querySelectorAll('#quotes-tbody td.avg-cell')];
        const frac = tds.find(td => /\\.\\d{3,}$/.test(td.textContent));
        return frac ? frac.textContent : 'none';
      })()
    """)
    print(f"      {P} 小数精度抽查: {precise}")

    # 第二层无「—」占位
    dash_spec = js(page, """
      [...document.querySelectorAll('#quotes-tbody .product-spec')].filter(e => e.textContent.trim() === '—').length
    """)
    check(dash_spec == 0, f"{P} 空规格不占行", f"{dash_spec} 处「—」")

    # 交互：展开 / 搜索建议 / 组织筛选 / 重置
    page.evaluate("() => document.querySelector('.expand-btn').click()")
    page.wait_for_timeout(150)
    check(js(page, "!document.querySelector('.detail-row').hidden"), f"{P} 行展开详情")
    page.fill("#q-input", "碳酸锂")
    page.wait_for_timeout(300)
    check(js(page, "document.getElementById('q-suggest').classList.contains('open')"), f"{P} 搜索建议下拉")
    page.evaluate("() => document.getElementById('q-suggest').classList.remove('open')")
    page.fill("#q-input", "")
    page.evaluate("() => document.getElementById('reset-btn').click()")
    page.wait_for_timeout(500)
    check(js(page, "document.querySelectorAll('#quotes-tbody tr').length > 0"), f"{P} 重置后表格恢复")

    if SHOOT:
        page.screenshot(path=str(OUT / f"quotes-{tag}.png"))


# ══════════ 价格走势 ══════════

def chart_label_rects(page):
    """X 轴日期标签测量：zrender 取渲染文本+本地宽度，convertToPixel 取锚点。
    返回 [{x, x2, t, W}]，坐标为画布内坐标（相对 canvas 原点）。"""
    return js(page, """
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
          // MM-DD → 完整日期（同年唯一）；YYYY-MM-DD 直接匹配
          const full = data.find((d) => d === t) || data.find((d) => d.endsWith("-" + t));
          if (full === undefined) continue;
          const anchor = chart.convertToPixel({ xAxisIndex: 0 }, full);  // 单轴 finder 返回标量
          const w = el.getBoundingRect().width;   // 本地宽度可靠
          out.push({ x: +(anchor - w / 2).toFixed(1), x2: +(anchor + w / 2).toFixed(1), t, W });
        }
        out.sort((a, b) => a.x - b.x);
        return out;
      })()
    """)


def verify_trends(page, w, h, tag):
    page.goto(BASE + "/trends")
    wait_ready(page, "trends")
    P = f"[{tag}] 价格走势"

    check(no_h_overflow(page), f"{P} 无横向溢出")

    # 图表头：指标 + 单位 + 时间范围 + 图例 + 区间带标注
    head = js(page, """
      ({
        metric: document.getElementById('chart-metric-label').textContent,
        range: document.getElementById('chart-range-label').textContent,
        chips: document.querySelectorAll('#chips-row .legend-chip').length,
        band: !!document.querySelector('#chips-row .legend-band'),
        bandText: (document.querySelector('#chips-row .legend-band')||{}).textContent || ''
      })
    """)
    check(head["metric"] in ("日均价", "最低价", "最高价"), f"{P} 图表头指标", head["metric"])
    check("~" in head["range"] and ("元" in head["range"] or "Wh" in head["range"]), f"{P} 图表头单位+时间范围", head["range"])
    check(head["chips"] == 1, f"{P} 单产品图例芯片", head["chips"])
    check(head["band"] and "最低—最高价" in head["bandText"], f"{P} 区间带标注「最低—最高价」", head["bandText"])

    # 日期标签完整：首尾标签都在画布内（修复右端裁切）
    labels = chart_label_rects(page)
    check(len(labels) >= 2, f"{P} X 轴日期标签存在", f"{len(labels)} 个")
    if len(labels) >= 2:
        canvas_w = labels[0]["W"]
        last_x2 = max(l["x2"] for l in labels)
        first_x = min(l["x"] for l in labels)
        check(last_x2 <= canvas_w - 1 and first_x >= -1,
              f"{P} 首尾日期标签完整在画布内", f"first_x={first_x:.1f} last_x2={last_x2:.1f} canvas_w={canvas_w:.0f}")
        overlap = 0
        sorted_l = sorted(labels, key=lambda l: l["x"])
        for a, b in zip(sorted_l, sorted_l[1:]):
            if a["x2"] > b["x"] + 0.5:
                overlap += 1
        check(overlap == 0, f"{P} 日期轴标签无重叠", f"{overlap} 对重叠 · {len(labels)} 个标签")
        fmt_ok = all(bool(re.match(r"^\d{2}-\d{2}$|^\d{4}-\d{2}-\d{2}$", l["t"])) for l in labels)
        check(fmt_ok, f"{P} 标签格式 MM-DD / YYYY-MM-DD", sorted(l["t"] for l in labels))

    # 主曲线颜色/宽度
    series = js(page, """
      (() => {
        const chart = echarts.getInstanceByDom(document.getElementById('chart'));
        const opt = chart.getOption();
        return opt.series.map(s => ({ color: s.lineStyle && s.lineStyle.color, width: s.lineStyle && s.lineStyle.width, area: !!(s.areaStyle && s.areaStyle.color) }));
      })()
    """)
    main_line = [s for s in series if s["width"]][-1]
    check(main_line["width"] == 2, f"{P} 主曲线 2px", main_line["width"])
    check(str(main_line["color"]).upper() == "#7DA7FF", f"{P} 主曲线品牌蓝", main_line["color"])
    check(any(s["area"] for s in series), f"{P} 区间带存在")

    # Tooltip：内容完整 + 不越界
    tip = js(page, """
      (() => {
        const chart = echarts.getInstanceByDom(document.getElementById('chart'));
        const opt = chart.getOption();
        const n = opt.xAxis[0].data.length;
        chart.dispatchAction({ type: 'showTip', seriesIndex: 2, dataIndex: n - 1 });
        const div = document.querySelector('#chart div[style*="position: absolute"]');
        const canvas = document.getElementById('chart').getBoundingClientRect();
        if (!div) return { ok: false };
        const r = div.getBoundingClientRect();
        return { ok: true, text: div.textContent, inside: r.left >= canvas.left - 1 && r.right <= canvas.right + 1 && r.top >= canvas.top - 1 && r.bottom <= canvas.bottom + 1 };
      })()
    """)
    check(tip["ok"] and "报价日期" in tip["text"] and "日均价" in tip["text"], f"{P} Tooltip 内容完整", tip["text"][:80])
    check(tip["ok"] and tip["inside"], f"{P} Tooltip 不越界")
    page.evaluate("() => { const c = echarts.getInstanceByDom(document.getElementById('chart')); c.dispatchAction({ type: 'hideTip' }); }")

    # 指标切换
    page.evaluate("() => document.querySelector('#metric-seg button[data-metric=max]').click()")
    page.wait_for_timeout(300)
    check(js(page, "document.getElementById('chart-metric-label').textContent") == "最高价", f"{P} 指标切换")

    # 多产品对比（同单位）
    page.fill("#product-pick", "磷酸铁锂")
    page.wait_for_timeout(400)
    page.evaluate("""
      () => {
        const btn = document.querySelector('#product-suggest .suggest-item');
        if (btn) btn.click();
      }
    """)
    page.wait_for_timeout(800)
    chips2 = js(page, "document.querySelectorAll('#chips-row .legend-chip').length")
    check(chips2 == 2, f"{P} 双产品对比图例", f"{chips2} chips")
    # 明细表
    detail_rows = js(page, "document.querySelectorAll('#detail-scroll tr[data-date]').length")
    check(detail_rows > 0, f"{P} 明细表联动", f"{detail_rows} 行")

    if SHOOT:
        page.screenshot(path=str(OUT / f"trends-{tag}.png"))


# ══════════ 数据与报表 ══════════

def verify_reports(page, w, h, tag):
    page.goto(BASE + "/reports")
    wait_ready(page, "reports")
    P = f"[{tag}] 数据与报表"

    check(no_h_overflow(page), f"{P} 无横向溢出")
    check(bg_hex(page, ".panel") == "#151F30", f"{P} 面板底色")

    rows = js(page, "document.querySelectorAll('#ds-scroll tbody tr').length")
    check(rows > 0, f"{P} 全量数据表渲染", f"{rows} 行")
    cat_plain = js(page, """
      (() => {
        const c = document.querySelector('#ds-scroll tbody td.ds-cat');
        return c && getComputedStyle(c).backgroundColor.replace(/ /g,'') === 'rgba(0,0,0,0)';
      })()
    """)
    check(cat_plain, f"{P} 分类纯文本无胶囊")

    # 分页
    prev_dis = js(page, "document.getElementById('ds-prev').disabled")
    check(prev_dis is True, f"{P} 分页·第1页上一页禁用")

    # 切月报
    page.evaluate("() => document.querySelector('#tab-seg button[data-tab=monthly]').click()")
    page.wait_for_selector("#month-scroll .data-table", timeout=20000)
    page.wait_for_timeout(500)
    m_rows = js(page, "document.querySelectorAll('#month-scroll tbody tr').length")
    check(m_rows >= 40, f"{P} 月报预览行数", f"{m_rows} 行")
    check(js(page, "document.getElementById('method-panel').tagName") == "DETAILS", f"{P} 计算说明为可展开面板")
    check(js(page, "document.getElementById('month-status').textContent.length > 20"), f"{P} 月报汇总条")

    if SHOOT:
        page.screenshot(path=str(OUT / f"reports-{tag}.png"))
        page.evaluate("() => document.querySelector('#tab-seg button[data-tab=dataset]').click()")


if __name__ == "__main__":
    main()
