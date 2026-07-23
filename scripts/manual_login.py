import asyncio,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/"src"))
from smm_collector.config import load_config
from smm_collector.browser import open_browser,close_browser
from smm_collector.authentication import save_manual_login
async def main():
 cfg=load_config(ROOT)
 if not cfg.login_url: raise RuntimeError("请先在 .env 填写 SMM_LOGIN_URL")
 pw,browser,context=await open_browser(cfg,headed=True,require_state=False); page=await context.new_page(); await page.goto(cfg.login_url)
 print("请在浏览器中合法完成登录及验证码，然后回到终端按 Enter。程序不会绕过验证。")
 await asyncio.to_thread(input); dest=await save_manual_login(cfg,context); print(f"登录状态已保存：{dest}"); await close_browser(pw,browser)
if __name__=="__main__": asyncio.run(main())

