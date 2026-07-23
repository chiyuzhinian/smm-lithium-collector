"""手动登录脚本（自动检测版）—— 检测登录成功后自动保存，无需按 Enter。"""
import asyncio, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from smm_collector.config import load_config
from smm_collector.browser import open_browser, close_browser
from smm_collector.authentication import save_manual_login

async def main():
    cfg = load_config(ROOT)
    if not cfg.login_url:
        raise RuntimeError("请先在 .env 填写 SMM_LOGIN_URL")

    pw, browser, context = await open_browser(cfg, headed=True, require_state=False)
    page = await context.new_page()
    await page.goto(cfg.login_url)

    login_host = "user.smm.cn"
    print(f"浏览器已打开登录页面。请在浏览器中完成登录（含验证码）。")
    print(f"等待登录完成（检测页面跳转离开 {login_host}）…")
    print(f"超时时间：5 分钟")

    # 等待登录成功：URL 离开登录域名，最多等 5 分钟
    try:
        await page.wait_for_function(
            f"() => !window.location.hostname.includes('{login_host}')",
            timeout=300_000  # 5 minutes
        )
        print("检测到登录跳转，正在保存登录状态…")
    except Exception:
        print("等待超时。如果你已完成登录，登录状态仍会被保存。")

    await page.wait_for_timeout(2000)  # 等页面稳定
    dest = await save_manual_login(cfg, context)
    print(f"登录状态已保存：{dest}")
    await close_browser(pw, browser)

if __name__ == "__main__":
    asyncio.run(main())
