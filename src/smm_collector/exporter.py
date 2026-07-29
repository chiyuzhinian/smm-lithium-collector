"""每日Excel/CSV导出 + 规范日报 + 近三日对比。"""
from __future__ import annotations
import logging, os, re
from collections import defaultdict
from pathlib import Path
import pandas as pd
from openpyxl import load_workbook

log = logging.getLogger("smm_collector.exporter")

COLUMNS = [
	"source", "market", "category", "product_name", "specification",
	"min_price", "max_price", "average_price", "change_value",
	"unit", "price_date", "collected_at", "source_url",
	"collection_method", "raw_text", "extra_fields",
	"record_hash", "validation_status", "validation_message",
]


def _excel_sheet_name(name: str) -> str:
	return re.sub(r'[\[\]:*?/\\]', '_', str(name))[:31]


def _safe_csv_name(name: str) -> str:
	return re.sub(r'[<>:"/\\|?*\s]+', '_', str(name).strip())


def _sorted_cats(series):
	seen = []
	for v in series:
		if v not in seen: seen.append(v)
	return seen


def _style(path):
	wb = load_workbook(path)
	for ws in wb.worksheets:
		ws.freeze_panes = "A2"; ws.auto_filter.ref = ws.dimensions
		for col in ws.columns:
			width = min(50, max(10, max(len(str(c.value or "")) for c in col) + 2))
			ws.column_dimensions[col[0].column_letter].width = width
	wb.save(path)


def _build_wide_table(db, config, target_date_str, log_ref):
	if not db or not config.get("enabled", True): return None, None
	window_days = config.get("window_days", 3)
	exclude_invalid = config.get("exclude_invalid_records", True)
	total_dates = db.get_distinct_price_date_count()
	dates = db.get_latest_price_dates(window_days)
	if not dates: return None, None
	dates_sorted = sorted(dates)
	records = db.get_records_by_price_dates(dates_sorted, exclude_invalid=exclude_invalid)
	if not records: return None, None
	from .price_statistics import product_key, compute_rolling_average
	groups = defaultdict(list)
	for r in records: groups[product_key(r)].append(r)
	wide_rows = []; f3 = t2 = o1 = z0 = 0
	for key, grp in groups.items():
		r0 = grp[0]
		row = {"分类": r0.get("category",""), "品名": r0.get("product_name",""),
		       "规格": r0.get("specification",""), "单位": r0.get("unit","")}
		for d in dates_sorted:
			dr = [r for r in grp if str(r.get("price_date","")) == d]
			row[f"{d[5:]}均价"] = dr[0].get("average_price") if dr else None
		avg, cnt = compute_rolling_average(grp, dates_sorted, include_warning=True, exclude_invalid=exclude_invalid)
		row["近三日均价"] = avg; row["有效天数"] = cnt
		if cnt >= 3: f3 += 1
		elif cnt == 2: t2 += 1
		elif cnt == 1: o1 += 1
		else: z0 += 1
		wide_rows.append(row)
	log_ref.info("Products: %d full3:%d two:%d one:%d zero:%d", len(wide_rows), f3, t2, o1, z0)
	return wide_rows, dates_sorted


def export_daily(rows, meta, export_root: Path, target_date, db=None, rolling_config=None):
	out = export_root / f"{target_date:%Y}" / f"{target_date:%m}"
	out.mkdir(parents=True, exist_ok=True)
	summary_dir = out / "每日汇总"; summary_dir.mkdir(parents=True, exist_ok=True)
	excel_dir = summary_dir / "Excel"; excel_dir.mkdir(parents=True, exist_ok=True)
	csv_dir = summary_dir / "CSV"; csv_dir.mkdir(parents=True, exist_ok=True)
	stem = f"SMM锂电现货价格_{target_date}"

	# 纵向DataFrame
	df_raw = pd.DataFrame(rows)
	for c in COLUMNS:
		if c not in df_raw.columns: df_raw[c] = None
	df_raw = df_raw[COLUMNS].sort_values(["category","product_name","price_date"])
	cats_in_data = _sorted_cats(df_raw["category"])

	# CSV
	csv = csv_dir / f"{stem}.csv"
	df_raw.to_csv(csv, index=False, encoding="utf-8-sig")
	for cat in cats_in_data:
		cd = out / _safe_csv_name(cat); cd.mkdir(parents=True, exist_ok=True)
		df_raw[df_raw.category == cat].to_csv(
			cd / f"SMM锂电现货价格_{_safe_csv_name(cat)}_{target_date}.csv", index=False, encoding="utf-8-sig")

	# Excel 1: 当日全部数据
	daily_xlsx = excel_dir / f"{stem}.xlsx"
	daily_tmp = daily_xlsx.with_suffix(".tmp.xlsx")
	with pd.ExcelWriter(daily_tmp, engine="openpyxl") as writer:
		df_raw.to_excel(writer, index=False, sheet_name="全部数据")
		for cat in cats_in_data:
			df_raw[df_raw.category == cat].to_excel(writer, index=False, sheet_name=_excel_sheet_name(cat))
		pd.DataFrame({"项目":["数据来源","采集日期","成功分类","数据行数"],
			"内容":["SMM",str(target_date),f"{len(meta.get('success_categories',[]))}个分类",f"{len(rows)}条"]}
		).to_excel(writer, index=False, sheet_name="采集说明")
	_style(daily_tmp); os.replace(daily_tmp, daily_xlsx)

	# 规范日报
	try:
		from .business_report import build_report, write_report_sheet
		all_smm = db.get_all_records() if db else rows
		report_df, quality = build_report(all_smm, target_date=target_date)
		report_xlsx = excel_dir / f"SMM锂电现货价格_规范日报_{target_date}.xlsx"
		with pd.ExcelWriter(report_xlsx, engine="openpyxl") as rw:
			write_report_sheet(rw, report_df)
		log.info("规范日报: %d/%d matched", quality["matched"], quality["total_required"])
	except Exception:
		log.exception("规范日报生成失败")

	# Excel 2: 近三日对比
	wide_rows, window_dates = _build_wide_table(db, rolling_config or {}, str(target_date), log)
	stem3 = f"SMM锂电现货价格_近三日对比_{target_date}"
	xlsx3 = excel_dir / f"{stem3}.xlsx"; tmp3 = xlsx3.with_suffix(".tmp.xlsx")
	if wide_rows:
		df_wide = pd.DataFrame(wide_rows)
		dc = [c for c in df_wide.columns if "均价" in c and "近三日" not in c]
		ordered = ["分类","品名","规格","单位"] + dc + ["近三日均价","有效天数"]
		df_wide = df_wide[[c for c in ordered if c in df_wide.columns]]
		df_wide = df_wide.sort_values(["分类","品名"]).reset_index(drop=True)
		with pd.ExcelWriter(tmp3, engine="openpyxl") as writer:
			df_wide.to_excel(writer, index=False, sheet_name="全部数据")
			for cat in sorted(df_wide["分类"].unique()):
				df_wide[df_wide["分类"]==cat].to_excel(writer, index=False, sheet_name=_excel_sheet_name(cat))
			pd.DataFrame({"项目":["数据来源","采集日期","窗口日期","产品数"],
				"内容":["SMM",str(target_date),"、".join(window_dates or []),f"{len(wide_rows)}个"]}
			).to_excel(writer, index=False, sheet_name="采集说明")
		_style(tmp3); os.replace(tmp3, xlsx3)

	# 历史汇总 + 固定汇总
	if meta.get("status") == "success":
		history = export_root / "SMM锂电现货价格_历史汇总.xlsx"
		base_df = df_raw.copy()
		old = pd.read_excel(history) if history.exists() else pd.DataFrame(columns=COLUMNS)
		merged = pd.concat([old, base_df], ignore_index=True).drop_duplicates(
			subset=["source","market","category","product_name","specification","unit","price_date"], keep="last")
		htmp = history.with_suffix(".tmp.xlsx"); merged.to_excel(htmp, index=False); _style(htmp); os.replace(htmp, history)
		fixed_dir = export_root / "固定汇总"; fixed_dir.mkdir(parents=True, exist_ok=True)
		fixed = fixed_dir / "SMM锂电现货价格_固定汇总.xlsx"; fixed_tmp = fixed.with_suffix(".tmp.xlsx")
		mc = _sorted_cats(merged["category"]); sc = len(meta.get("success_categories",[]))
		cs = "、".join(mc[:5]) + ("…等" if len(mc)>5 else "")
		with pd.ExcelWriter(fixed_tmp, engine="openpyxl") as writer:
			merged.to_excel(writer, index=False, sheet_name="全部数据")
			for cat in mc:
				merged[merged.category==cat].to_excel(writer, index=False, sheet_name=_excel_sheet_name(cat))
			pd.DataFrame({"项目":["数据来源","最后更新日期","数据范围","采集状态"],
				"内容":["SMM",str(target_date),cs,f"共{sc}个分类完整成功"]}
			).to_excel(writer, index=False, sheet_name="采集说明")
		_style(fixed_tmp); os.replace(fixed_tmp, fixed)

	# OneDrive
	onedrive = os.getenv("ONEDRIVE_EXPORT_DIR","")
	if onedrive:
		try:
			import shutil; od = Path(onedrive)/f"{target_date:%Y}"/f"{target_date:%m}"; od.mkdir(parents=True,exist_ok=True)
			shutil.copy2(daily_xlsx, od/daily_xlsx.name)
		except Exception: pass

	return daily_xlsx, csv
