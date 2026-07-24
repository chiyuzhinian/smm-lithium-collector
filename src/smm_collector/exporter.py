from __future__ import annotations
import logging, os, re
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
ROLLING_COLUMNS = ["three_day_average_price", "three_day_valid_count"]

CN_COLUMNS = {
	"source": "数据来源", "market": "市场", "category": "分类",
	"product_name": "品名", "specification": "规格",
	"min_price": "最低价", "max_price": "最高价", "average_price": "平均价",
	"change_value": "涨跌", "unit": "单位", "price_date": "价格日期",
	"collected_at": "采集时间", "source_url": "来源URL",
	"collection_method": "采集方式", "raw_text": "原始文本",
	"extra_fields": "额外字段", "record_hash": "记录哈希",
	"validation_status": "校验状态", "validation_message": "校验信息",
	"three_day_average_price": "近三日均价",
	"three_day_valid_count": "近三日有效天数",
}


def _excel_sheet_name(name: str) -> str:
	s = re.sub(r'[\[\]:*?/\\]', '_', str(name))
	return s[:31]


def _safe_csv_name(name: str) -> str:
	return re.sub(r'[<>:"/\\|?*\s]+', '_', str(name).strip())


def _sorted_categories(df):
	seen = []
	for cat in df["category"]:
		if cat not in seen:
			seen.append(cat)
	return seen


def _frame(rows):
	df = pd.DataFrame(rows)
	for c in COLUMNS:
		if c not in df: df[c] = None
	for c in ROLLING_COLUMNS:
		if c not in df: df[c] = None
	all_cols = COLUMNS + ROLLING_COLUMNS
	return df[[c for c in all_cols if c in df.columns]]


def _style(path):
	wb = load_workbook(path)
	for ws in wb.worksheets:
		ws.freeze_panes = "A2"
		ws.auto_filter.ref = ws.dimensions
		for col in ws.columns:
			width = min(60, max(10, max(len(str(c.value or "")) for c in col) + 2))
			ws.column_dimensions[col[0].column_letter].width = width
	wb.save(path)


def _build_rolling_data(db, config: dict, target_date_str: str, log_ref):
	if not db or not config.get("enabled", True):
		return None
	window_days = config.get("window_days", 3)
	exclude_invalid = config.get("exclude_invalid_records", True)
	total_dates = db.get_distinct_price_date_count()
	dates = db.get_latest_price_dates(window_days)
	if not dates:
		log_ref.warning("SQLite 无价格数据，跳过近N日导出")
		return None
	log_ref.info("数据库价格日期数：%d，本次统计日期：%s", total_dates, "、".join(dates))
	if len(dates) < window_days:
		log_ref.info("当前仅有%d个价格日期，近三日均价按照现有%d天数据计算", len(dates), len(dates))
	records = db.get_records_by_price_dates(dates, exclude_invalid=exclude_invalid)
	log_ref.info("查询到 %d 条记录", len(records))
	if not records:
		return None
	from .price_statistics import enrich_with_rolling_average, group_by_product
	enriched = enrich_with_rolling_average(records, dates, config)
	groups = group_by_product(enriched)
	full3 = two = one = zero = 0
	for g_rows in groups.values():
		counts = set()
		for r in g_rows:
			if r.get("three_day_valid_count", 0) > 0:
				counts.add(r["three_day_valid_count"])
		vc = max(counts) if counts else 0
		if vc >= 3: full3 += 1
		elif vc == 2: two += 1
		elif vc == 1: one += 1
		else: zero += 1
	log_ref.info("产品分组数：%d，完整三日：%d，两日：%d，单日：%d，无效：%d", len(groups), full3, two, one, zero)
	return enriched, dates, total_dates


def export_daily(rows, meta, export_root: Path, target_date, db=None, rolling_config=None):
	out = export_root / f"{target_date:%Y}" / f"{target_date:%m}"
	out.mkdir(parents=True, exist_ok=True)
	summary_dir = out / "每日汇总"
	summary_dir.mkdir(parents=True, exist_ok=True)
	excel_dir = summary_dir / "Excel"
	excel_dir.mkdir(parents=True, exist_ok=True)
	csv_summary_dir = summary_dir / "CSV"
	csv_summary_dir.mkdir(parents=True, exist_ok=True)
	stem = f"SMM锂电现货价格_{target_date}"
	xlsx = excel_dir / f"{stem}.xlsx"
	tmp = xlsx.with_suffix(".tmp.xlsx")

	rolling_data = _build_rolling_data(db, rolling_config or {}, str(target_date), log)
	if rolling_data:
		enriched_rows, window_dates, total_dates = rolling_data
		export_rows = [r for r in enriched_rows if str(r.get("price_date", "")) in window_dates]
	else:
		export_rows = rows

	df = _frame(export_rows)
	sort_cols = ["category", "product_name", "specification", "unit", "price_date"]
	existing_sort = [c for c in sort_cols if c in df.columns]
	if existing_sort:
		df = df.sort_values(existing_sort).reset_index(drop=True)
	cats = _sorted_categories(df)

	with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
		df.to_excel(writer, index=False, sheet_name="全部数据")
		for cat in cats:
			df[df.category == cat].to_excel(writer, index=False, sheet_name=_excel_sheet_name(cat))
		pd.DataFrame({
			"项目": ["数据来源", "采集日期", "成功分类", "失败分类", "是否完整成功"],
			"内容": ["SMM", str(target_date),
			        "、".join(meta.get("success_categories", [])),
			        "、".join(meta.get("failed_categories", [])),
			        str(meta.get("status") == "success")]
		}).to_excel(writer, index=False, sheet_name="采集说明")
	_style(tmp)

	from openpyxl import load_workbook as _lw
	wb = _lw(tmp)
	for ws in wb.worksheets:
		if ws.title in ("采集说明",): continue
		for col_idx, cell in enumerate(ws[1], start=1):
			cn_name = CN_COLUMNS.get(str(cell.value or ""), str(cell.value or ""))
			cell.value = cn_name
	wb.save(tmp)
	os.replace(tmp, xlsx)

	csv = csv_summary_dir / f"{stem}.csv"
	df.to_csv(csv, index=False, encoding="utf-8-sig")
	for cat in cats:
		cat_dir = out / _safe_csv_name(cat)
		cat_dir.mkdir(parents=True, exist_ok=True)
		df[df.category == cat].to_csv(
			cat_dir / f"SMM锂电现货价格_{_safe_csv_name(cat)}_{target_date}.csv",
			index=False, encoding="utf-8-sig")

	if meta.get("status") == "success":
		history = export_root / "SMM锂电现货价格_历史汇总.xlsx"
		base_df = pd.DataFrame(rows)
		for c in COLUMNS:
			if c not in base_df.columns: base_df[c] = None
		base_df = base_df[COLUMNS]
		old = pd.read_excel(history) if history.exists() else pd.DataFrame(columns=COLUMNS)
		merged = pd.concat([old, base_df], ignore_index=True).drop_duplicates(
			subset=["source", "market", "category", "product_name", "specification", "unit", "price_date"], keep="last")
		htmp = history.with_suffix(".tmp.xlsx")
		merged.to_excel(htmp, index=False)
		_style(htmp)
		os.replace(htmp, history)

		fixed_dir = export_root / "固定汇总"
		fixed_dir.mkdir(parents=True, exist_ok=True)
		fixed = fixed_dir / "SMM锂电现货价格_固定汇总.xlsx"
		fixed_tmp = fixed.with_suffix(".tmp.xlsx")
		merged_cats = _sorted_categories(merged)
		success_count = len(meta.get("success_categories", []))
		cat_summary = "、".join(merged_cats[:5]) + ("…等" if len(merged_cats) > 5 else "")
		with pd.ExcelWriter(fixed_tmp, engine="openpyxl") as writer:
			merged.to_excel(writer, index=False, sheet_name="全部数据")
			for cat in merged_cats:
				merged[merged.category == cat].to_excel(writer, index=False, sheet_name=_excel_sheet_name(cat))
			pd.DataFrame({
				"项目": ["数据来源", "最后更新日期", "数据范围", "采集状态"],
				"内容": ["SMM", str(target_date), cat_summary, f"共{success_count}个分类完整成功"]
			}).to_excel(writer, index=False, sheet_name="采集说明")
		_style(fixed_tmp)
		os.replace(fixed_tmp, fixed)

	return xlsx, csv
