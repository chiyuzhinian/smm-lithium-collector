from __future__ import annotations
import argparse, asyncio, json, uuid, re
from datetime import date, datetime
from pathlib import Path
from . import __version__
from .browser import open_browser,close_browser
from .authentication import looks_logged_out
from .category_navigator import CategoryNavigator,exhaust_page,discover_categories
from .config import load_config
from .database import Database
from .exporter import export_daily
from .logger import setup_logging
from .network_capture import NetworkCapture
from .parser import parse_html_tables,parse_category_section
from .validator import validate_row,run_status

def _safe_filename(name: str) -> str:
	return re.sub(r'[<>:"/\\|?*\s]+', '_', name.strip())

async def collect(target_date: date, category=None, headed=False, dry_run=False):
	cfg = load_config()
	log = setup_logging(cfg.root)
	if not cfg.target_url:
		raise RuntimeError("请在 .env 填写 SMM_TARGET_URL")
	started = datetime.now()
	stamp = started.strftime("%Y-%m-%d_%H%M%S")
	rows = []
	pw = browser = context = None
	try:
		pw, browser, context = await open_browser(cfg, headed)
		page = await context.new_page()
		network = NetworkCapture(cfg.root / "data/raw/network")
		network.attach(page)
		await page.goto(cfg.target_url, wait_until="domcontentloaded", timeout=60000)
		if await looks_logged_out(page, cfg):
			diag = cfg.root / "data/screenshots" / f"login_expired_{stamp}.png"
			await page.screenshot(path=str(diag), full_page=True)
			(diag.with_suffix(".html")).write_text(await page.content(), encoding="utf-8")
			raise RuntimeError("登录状态失效或出现验证，请重新运行 manual_login.py")
		if cfg.categories_mode == "manual":
			all_categories = [c for c in cfg.categories_items if not category or c["name"] == category]
			if category and category not in [c["name"] for c in cfg.categories_items]:
				raise ValueError(f"未知分类：{category}")
		else:
			heading_selector = cfg.selectors.get("category", {}).get("item")
			if not heading_selector:
				raise RuntimeError("自动发现分类需要配置 category.item 选择器")
			await page.wait_for_timeout(3000)
			found = False
			for attempt in range(30):
				cnt = await page.locator(heading_selector).count()
				if cnt > 0: found = True; break
				await asyncio.sleep(1)
			if not found:
				diag = cfg.root / "data/screenshots" / f"no_categories_{stamp}.png"
				await page.screenshot(path=str(diag), full_page=True)
				(diag.with_suffix(".html")).write_text(await page.content(), encoding="utf-8")
				raise RuntimeError(f"等待超时，截图已保存至 {diag}")
			await page.wait_for_timeout(1000)
			discovered = await discover_categories(page, heading_selector)
			if not discovered:
				raise RuntimeError("页面未发现任何分类")
			log.info("自动发现 %d 个分类", len(discovered))
			if category:
				all_categories = [c for c in discovered if c["name"] == category]
				if not all_categories: raise ValueError(f"未找到分类：{category}")
			else:
				all_categories = discovered
		expected = [c["name"] for c in all_categories]
		meta = {"run_id": str(uuid.uuid4()), "started_at": started.isoformat(timespec="seconds"),
		        "target_date": str(target_date), "expected_categories": expected,
		        "discover_mode": cfg.categories_mode, "success_categories": [],
		        "failed_categories": [], "errors": {}, "version": __version__,
		        "page_url": page.url, "page_title": await page.title(), "login_status": "authenticated"}
		navigator = CategoryNavigator(page, all_categories, cfg.selectors, cfg.settings["browser"]["timeout_ms"])
		async def one(cat):
			name = cat["name"]
			log.info("当前采集分类：%s", name)
			await exhaust_page(page, cfg.selectors, cfg.settings["collector"]["max_scroll_attempts"])
			html = await page.content()
			h_sel = cfg.selectors.get("category", {}).get("item")
			parsed = (parse_category_section(html, name, h_sel, page.url, datetime.now())
			          if cfg.selectors.get("category", {}).get("mode") == "section" and h_sel
			          else parse_html_tables(html, name, page.url, datetime.now()))
			folder = cfg.path("raw_dir") / f"{target_date:%Y/%m/%d}" / _safe_filename(name)
			folder.mkdir(parents=True, exist_ok=True)
			base = folder / f"smm_{cat['code']}_{stamp}"
			base.with_suffix(".html").write_text(html, encoding="utf-8")
			await page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
			base.with_suffix(".json").write_text(json.dumps(parsed, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
			if not parsed: raise RuntimeError("表格解析为空")
			clean = [validate_row(x, target_date) for x in parsed]
			rows.extend(clean)
			meta.setdefault("category_counts", {})[name] = {
				"raw": len(parsed), "clean": len(clean),
				"abnormal": sum(x["validation_status"] != "valid" for x in clean)}
			return len(clean)
		ok, failed = await navigator.traverse(one, cfg.settings["collector"]["continue_on_category_failure"])
		await network.drain()
		meta["success_categories"] = list(ok); meta["failed_categories"] = list(failed); meta["errors"] = failed
	finally:
		if browser: await close_browser(pw, browser)
	meta["status"] = run_status(expected, meta["success_categories"])
	meta["finished_at"] = datetime.now().isoformat(timespec="seconds")
	meta["total_raw_rows"] = sum(x["raw"] for x in meta.get("category_counts", {}).values())
	meta["total_clean_rows"] = len(rows)
	run_file = cfg.path("raw_dir") / f"{target_date:%Y/%m/%d}" / f"run_metadata_{stamp}.json"
	run_file.parent.mkdir(parents=True, exist_ok=True)
	run_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
	db = Database(cfg.path("database_path"))
	stats = {}
	if not dry_run: stats = db.upsert(rows); db.save_run(meta)
	if rows:
		rolling_cfg = cfg.settings.get("rolling_price_export", {})
		xlsx, csv = export_daily(rows, meta, cfg.path("export_dir"), target_date, db=db, rolling_config=rolling_cfg)
		log.info("导出：%s；%s", xlsx, csv)
	sync_stats = None
	if not dry_run and rows:
		from .mysql_database import AUTO_SYNC
		if AUTO_SYNC:
			try:
				from .synchronizer import sync as sync_to_mysql
				from .data_quality import generate_daily_report
				log.info("开始 MySQL 自动同步…")
				sync_stats = sync_to_mysql(cfg.path("database_path"), date_from=target_date, date_to=target_date)
				meta["mysql_sync"] = sync_stats
				log.info("MySQL同步完成 i=%d u=%d s=%d f=%d",
				         sync_stats.get("inserted", 0), sync_stats.get("updated", 0),
				         sync_stats.get("skipped", 0), sync_stats.get("failed", 0))
				if sync_stats.get("status") == "failed":
					log.warning("MySQL同步失败: %s", sync_stats.get("error", ""))
					if meta["status"] == "success":
						meta["status"] = "partial_success"
						meta.setdefault("errors", {})["mysql_sync"] = sync_stats.get("error", "")
				generate_daily_report(meta, sync_stats, cfg.path("export_dir"))
			except Exception:
				log.exception("MySQL 同步异常，采集结果不受影响")
	log.info("最终状态=%s 分类=%s db=%s sync=%s",
	         meta["status"], meta.get("category_counts"), stats,
	         sync_stats.get("status") if sync_stats else "未执行")
	# 钉钉通知
	try:
		from .notifier import send_daily_notification
		await send_daily_notification(meta, sync_stats)
	except Exception:
		pass
	return meta

def cli():
	p = argparse.ArgumentParser(description="SMM 锂电现货价格每日采集器")
	p.add_argument("--date", type=date.fromisoformat, default=date.today(), help="采集日期（默认今天）")
	p.add_argument("--category", help="仅采集指定分类")
	p.add_argument("--headed", action="store_true", help="显示浏览器窗口")
	p.add_argument("--dry-run", action="store_true", help="试运行，不写入数据库")
	args = p.parse_args()
	meta = asyncio.run(collect(args.date, args.category, args.headed, args.dry_run))
	raise SystemExit(0 if meta["status"] == "success" else 2 if meta["status"] == "partial_success" else 1)

if __name__ == "__main__":
	cli()
