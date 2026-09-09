#!/usr/bin/env python
"""门户视觉验收截图：登录 → 三栏目多视口截图（before/after 通用）。

用法:
  .venv/bin/python scripts/shoot_portal.py <base_url> <out_dir> [--page quotes|trends|reports|all]
  .venv/bin/python scripts/shoot_portal.py http://127.0.0.1:8899 data/screenshots/v3/before
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8899"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "data/screenshots/v3/before")
ONLY = sys.argv[sys.argv.index("--page") + 1] if "--page" in sys.argv else "all"
USER, PWD = "huayou", "DevPass2026"

PAGES = {"quotes": "/", "trends": "/trends", "reports": "/reports"}
VIEWPORTS = [(1440, 900), (1920, 1080), (1280, 800), (375, 812)]


def wait_ready(page, name):
    if name == "quotes":
        page.wait_for_selector(".data-table tbody tr", timeout=30000)
    elif name == "trends":
        page.wait_for_selector("#chart canvas", timeout=30000)
        page.wait_for_timeout(1800)  # ECharts 动画完成
    elif name == "reports":
        page.wait_for_selector("#tab-dataset .data-table", timeout=30000)


def shoot(page, name, tag, extra_wait=0):
    wait_ready(page, name)
    if extra_wait:
        page.wait_for_timeout(extra_wait)
    out = OUT / f"{name}-{tag}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out), full_page=False)
    print("saved", out)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto(BASE + "/login")
        page.fill("#username", USER)
        page.fill("#password", PWD)
        page.click("#login-btn")
        page.wait_for_url("**/", timeout=15000)

        pages = list(PAGES.items()) if ONLY == "all" else [
            (n, p) for n, p in PAGES.items() if n == ONLY]

        for name, path in pages:
            for w, h in VIEWPORTS:
                page.set_viewport_size({"width": w, "height": h})
                page.goto(BASE + path)
                tag = f"{w}x{h}"
                shoot(page, name, tag)
                # 125% 缩放（仅桌面尺寸）
                if w >= 1280:
                    page.set_viewport_size({"width": int(w / 1.25), "height": int(h / 1.25)})
                    page.evaluate("() => { document.body.style.zoom = 1.25; window.dispatchEvent(new Event('resize')); }")
                    page.wait_for_timeout(1200)
                    shoot(page, name, f"{w}x{h}-zoom125")
                    page.evaluate("() => { document.body.style.zoom = 1; }")
        browser.close()


if __name__ == "__main__":
    main()
