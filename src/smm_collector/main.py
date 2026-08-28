"""SMM锂电现货采集主流程 + 附加数据源（含延迟重试）。"""
from __future__ import annotations
import argparse, asyncio, json, uuid, re
from datetime import date, datetime
from pathlib import Path
from . import __version__
from .browser import open_browser,close_browser
from .authentication import looks_logged_out
from .category_navigator import CategoryNavigator,exhaust_page,discover_categories
from .config import load_config, load_portal_config
from .database import Database
from .exporter import export_daily
from .logger import setup_logging
from .network_capture import NetworkCapture
from .parser import parse_html_tables,parse_category_section
from .validator import validate_row,run_status,modal_price_date

def _safe_filename(name: str) -> str:
	return re.sub(r'[<>:"/\\|?*\s]+', '_', name.strip())

def _ops_event(event_type: str, level: str, message: str) -> None:
	"""写运维事件到 auth.db（异常安全，auth.db 故障绝不影响采集结果）。"""
	try:
		from .ops_events import write_event
		write_event(event_type, level, message)
	except Exception:
		pass

async def collect(target_date: date, category=None, headed=False, dry_run=False,
                  calibrate_date=True):
	cfg = load_config(); log = setup_logging(cfg.root)
	if not cfg.target_url: raise RuntimeError("请在 .env 填写 SMM_TARGET_URL")
	started = datetime.now(); stamp = started.strftime("%Y-%m-%d_%H%M%S")
	_ops_event("COLLECTOR_STARTED", "info", f"开始采集 target_date={target_date}")
	rows = []; pw = browser = context = None
	try:
		pw, browser, context = await open_browser(cfg, headed)
		page = await context.new_page()
		network = NetworkCapture(cfg.root/"data/raw/network"); network.attach(page)
		await page.goto(cfg.target_url, wait_until="domcontentloaded", timeout=60000)
		if await looks_logged_out(page, cfg):
			diag = cfg.root/"data/screenshots"/f"login_expired_{stamp}.png"
			await page.screenshot(path=str(diag), full_page=True)
			(diag.with_suffix(".html")).write_text(await page.content(), encoding="utf-8")
			raise RuntimeError("登录状态失效")
		if cfg.categories_mode == "manual":
			all_categories = [c for c in cfg.categories_items if not category or c["name"]==category]
		else:
			h_sel = cfg.selectors.get("category",{}).get("item")
			if not h_sel: raise RuntimeError("需要category.item选择器")
			await page.wait_for_timeout(3000)
			for _ in range(30):
				if await page.locator(h_sel).count()>0: break
				await asyncio.sleep(1)
			await page.wait_for_timeout(1000)
			discovered = await discover_categories(page, h_sel)
			if not discovered:
				# 诊断优先：未发现分类时保存现场，便于排查登录态/页面结构问题
				diag = cfg.root/"data/screenshots"/f"no_categories_{stamp}.png"
				try:
					await page.screenshot(path=str(diag), full_page=True)
					diag.with_suffix(".html").write_text(await page.content(), encoding="utf-8")
				except Exception as e:
					log.warning("保存未发现分类诊断文件失败: %s", e)
				raise RuntimeError("未发现分类")
			log.info("发现%d个分类", len(discovered))
			all_categories = [c for c in discovered if not category or c["name"]==category]
		expected = [c["name"] for c in all_categories]
		meta = {"run_id":str(uuid.uuid4()),"started_at":started.isoformat(timespec="seconds"),
			"target_date":str(target_date),"expected_categories":expected,
			"discover_mode":cfg.categories_mode,"success_categories":[],"failed_categories":[],
			"errors":{},"version":__version__,"page_url":page.url,"page_title":await page.title(),
			"login_status":"authenticated"}
		navigator = CategoryNavigator(page, all_categories, cfg.selectors, cfg.settings["browser"]["timeout_ms"])
		async def one(cat):
			name = cat["name"]; log.info("采集: %s", name)
			await exhaust_page(page, cfg.selectors, cfg.settings["collector"]["max_scroll_attempts"])
			html = await page.content()
			parsed = (parse_category_section(html, name, h_sel, page.url, datetime.now())
				if cfg.selectors.get("category",{}).get("mode")=="section" and h_sel
				else parse_html_tables(html, name, page.url, datetime.now()))
			folder = cfg.path("raw_dir")/f"{target_date:%Y/%m/%d}"/_safe_filename(name)
			folder.mkdir(parents=True, exist_ok=True)
			base = folder/f"smm_{cat['code']}_{stamp}"
			base.with_suffix(".html").write_text(html, encoding="utf-8")
			await page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
			base.with_suffix(".json").write_text(json.dumps(parsed,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
			if not parsed: raise RuntimeError("解析为空")
			clean = [validate_row(x, target_date) for x in parsed]; rows.extend(clean)
			meta.setdefault("category_counts",{})[name] = {"raw":len(parsed),"clean":len(clean),"abnormal":sum(x["validation_status"]!="valid" for x in clean)}
			return len(clean)
		ok, failed = await navigator.traverse(one, cfg.settings["collector"]["continue_on_category_failure"])
		await network.drain()
		meta["success_categories"] = list(ok); meta["failed_categories"] = list(failed); meta["errors"] = failed
	finally:
		if browser: await close_browser(pw, browser)

	meta["status"] = run_status(expected, meta["success_categories"])
	meta["finished_at"] = datetime.now().isoformat(timespec="seconds")
	meta["total_raw_rows"] = sum(x["raw"] for x in meta.get("category_counts",{}).values())
	meta["total_clean_rows"] = len(rows)
	# 页面数据日期校准：SMM 未发布当日数据时页面仍是前一交易日数据（周一上午=上周五），
	# 属正常现象。校验/门控/验证以页面数据日期为准，避免 FAIL 误报与临时快照噪音。
	vcfg0 = cfg.settings.get("validation") or {}
	if calibrate_date:
		data_date = modal_price_date(rows, target_date,
		                             max_stale_days=int(vcfg0.get("max_stale_data_days", 14)))
		if data_date != target_date:
			log.info("页面数据日期=%s（目标日期=%s），按页面日期校准", data_date, target_date)
			rows = [validate_row(r, data_date) for r in rows]
	else:
		data_date = target_date
	meta["data_date"] = str(data_date)
	run_file = cfg.path("raw_dir")/f"{target_date:%Y/%m/%d}"/f"run_metadata_{stamp}.json"
	run_file.parent.mkdir(parents=True, exist_ok=True)
	run_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

	db = Database(cfg.path("database_path"))
	stats = {}; add_meta = {}
	if not dry_run: stats = db.upsert(rows); db.save_run(meta)

	# 附加数据源（含延迟重试）
	additional_cfg = cfg.additional_sources
	if additional_cfg.get("enabled",False) and not dry_run:
		from .additional_sources import collect_source
		pw2, br2, ctx2 = await open_browser(cfg, headed)
		p2 = await ctx2.new_page()
		for src_cfg in additional_cfg.get("items",[]):
			try:
				s_rows, s_meta = await collect_source(p2, src_cfg, target_date, stamp, cfg.path("raw_dir"))
				add_meta[src_cfg.get("code","?")] = s_meta
				if s_rows: db.upsert(s_rows); rows.extend(s_rows)
				log.info("附加[%s]: %d行 %s", src_cfg.get("code"), len(s_rows) if s_rows else 0, s_meta["status"])
			except Exception as e:
				log.warning("附加[%s]失败: %s", src_cfg.get("code"), e)
		meta["additional_sources"] = add_meta
		await close_browser(pw2, br2)

	meta["total_clean_rows"] = len(rows)
	# 导出（固定汇总门控用规范分类全集 + 阈值；配置缺失时退化为旧逻辑）
	portal_cfg = load_portal_config(cfg.root)
	meta["canonical_categories"] = portal_cfg.get("canonical_categories")
	meta["summary_gate"] = portal_cfg.get("summary_gate")
	if rows:
		try:
			xlsx, csv = export_daily(rows, meta, cfg.path("export_dir"), target_date, db=db,
		                         data_date=data_date)
			log.info("导出：%s", xlsx)
		except Exception: log.exception("导出失败")
		try:
			if meta.get("summary_decision"):
				from .data_quality import generate_status_manifest
				generate_status_manifest(meta, rows, meta["summary_decision"], cfg.path("export_dir"))
				log.info("数据状态manifest已生成 decision=%s", meta["summary_decision"].get("decision"))
				_ops_event("FIXED_SUMMARY_UPDATED", "info",
					f"固定汇总 decision={meta['summary_decision'].get('decision')}")
		except Exception: log.exception("数据状态manifest生成失败")
		# 每日统一数据验证（后台 9 层，写 logs/validation/；FAIL 只记录，
		# 固定汇总由门控强制不覆盖正式文件，原始数据永不删除）
		try:
			vcfg = cfg.settings.get("validation") or {}
			if vcfg.get("enabled", True):
				from .daily_validation import run_daily_validation
				vres = run_daily_validation(target_date, cfg.path("database_path"),
				                            cfg.path("export_dir"), portal_cfg, vcfg,
				                            data_date=data_date)
				meta["daily_validation"] = {"verdict": vres["verdict"],
				                            "log_file": vres["log_file"]}
				_ops_event("VALIDATION_VERDICT",
					"info" if vres["verdict"]=="PASS" else "warning" if vres["verdict"]=="WARNING" else "error",
					f"数据验证 {vres['verdict']} error={vres['counts']['error']} warning={vres['counts']['warning']}")
				if vres["verdict"] == "FAIL":
					head = "；".join(i["message"] for i in vres["issues"]
					                 if i["level"] == "error")[:500]
					log.error("每日验证 FAIL: %s", head)
				else:
					log.info("每日验证 %s（error=%d warning=%d）", vres["verdict"],
					         vres["counts"]["error"], vres["counts"]["warning"])
		except Exception: log.exception("每日验证执行失败")
	# MySQL同步
	sync_stats = None
	if not dry_run and rows:
		from .mysql_database import AUTO_SYNC
		if AUTO_SYNC:
			try:
				from .synchronizer import sync as sync_to_mysql
				from .data_quality import generate_daily_report
				# 用校准后的数据日期同步，而非目标日期：页面未发布当日数据时
				# price_date==data_date（通常 D-1），按 target_date 同步会命中空窗口
				sync_stats = sync_to_mysql(cfg.path("database_path"), date_from=data_date, date_to=data_date)
				meta["mysql_sync"] = sync_stats
				generate_daily_report(meta, sync_stats, cfg.path("export_dir"))
			except Exception: log.exception("MySQL同步异常")
	log.info("状态=%s db=%s sync=%s", meta["status"], stats, sync_stats.get("status") if sync_stats else "未执行")
	# 钉钉
	try:
		from .notifier import send_daily_notification
		await send_daily_notification(meta, sync_stats, str(cfg.path("database_path")))
	except Exception: pass
	_ops_event("COLLECTOR_FINISHED",
		"info" if meta["status"]=="success" else "warning",
		f"采集结束 status={meta['status']} rows={meta.get('total_clean_rows')}")
	return meta

def cli():
	p = argparse.ArgumentParser(description="SMM锂电现货每日采集")
	p.add_argument("--date", type=date.fromisoformat, default=None)
	p.add_argument("--category"); p.add_argument("--headed", action="store_true")
	p.add_argument("--dry-run", action="store_true")
	args = p.parse_args()
	target_date = args.date or date.today()
	# 定时任务不传 --date：按页面数据日期校准（当日未发布时按前一交易日）。
	# 手动指定 --date：尊重显式日期，不校准。
	meta = asyncio.run(collect(target_date, args.category, args.headed, args.dry_run,
	                           calibrate_date=args.date is None))
	raise SystemExit(0 if meta["status"]=="success" else 2 if meta["status"]=="partial_success" else 1)

if __name__ == "__main__": cli()
