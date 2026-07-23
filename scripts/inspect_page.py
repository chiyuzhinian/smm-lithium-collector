import asyncio,json,sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/"src"))
from smm_collector.config import load_config
from smm_collector.browser import open_browser,close_browser
from smm_collector.category_navigator import discover_categories
from smm_collector.network_capture import NetworkCapture

async def main():
	cfg=load_config(ROOT)
	if not cfg.target_url:
		raise RuntimeError("请在 .env 填写 SMM_TARGET_URL")
	pw,browser,ctx=await open_browser(cfg,headed=True)
	page=await ctx.new_page()
	stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
	out=cfg.root/"data/raw/inspection"/stamp; out.mkdir(parents=True,exist_ok=True)
	net=NetworkCapture(cfg.root/"data/raw/network"); net.attach(page)
	await page.goto(cfg.target_url,wait_until="domcontentloaded"); await page.wait_for_timeout(3000)
	(out/"page.html").write_text(await page.content(),encoding="utf-8")
	await page.screenshot(path=str(out/"page.png"),full_page=True)

	# 自动发现所有分类
	heading_selector=cfg.selectors.get("category",{}).get("item")
	discovered=[]
	if heading_selector:
		discovered=await discover_categories(page,heading_selector)

	# 基本元素统计
	counts={}
	for s in ("table","thead","tbody","tr","iframe","button"):
		try: counts[s]=await page.locator(s).count(); counts[s+"_visible"]=await page.locator(f"{s}:visible").count()
		except Exception: counts[s]="error"

	# 动态发现的分类详情
	category_details=[]
	if heading_selector:
		elements=page.locator(heading_selector)
		for i in range(await elements.count()):
			try:
				el=elements.nth(i)
				text=(await el.inner_text()).strip()
				el_id=(await el.get_attribute("id") or "")
				# Count rows in the following table
				table=el.locator("xpath=following::table[1]")
				row_count=(await table.locator("tbody tr").count()) if await table.count() else 0
				category_details.append({"name":text,"id":el_id,"row_count":row_count})
			except Exception: pass

	await net.drain()
	report={
		"title":await page.title(),"url":page.url,"timestamp":stamp,
		"counts":counts,
		"auto_discovered_categories":{
			"total":len(discovered),
			"names":[d["name"] for d in discovered],
			"selector_used":heading_selector,
		},
		"category_details":category_details,
		"network_candidates":net.candidates,
		"note":"自动发现全部分类并统计行数。未记录请求头、Cookie、Token 或 Authorization。"
	}
	(out/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
	print(json.dumps(report,ensure_ascii=False,indent=2))
	await close_browser(pw,browser)

if __name__=="__main__":
	asyncio.run(main())
