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
CN_COLUMNS = {
	"source": "数据来源", "market": "市场", "category": "分类",
	"product_name": "品名", "specification": "规格",
	"min_price": "最低价", "max_price": "最高价", "average_price": "平均价",
	"change_value": "涨跌", "unit": "单位", "price_date": "价格日期",
	"collected_at": "采集时间", "validation_status": "校验状态",
}


def _excel_sheet_name(name: str) -> str:
	return re.sub(r'[\[\]:*?/\\]', '_', str(name))[:31]


def _safe_csv_name(name: str) -> str:
	return re.sub(r'[<>:"/\\|?*\s]+', '_', str(name).strip())


def _sorted_categories(df):
	seen = []
	for cat in df.get("分类", df.get("category", pd.Series())):
		if cat not in seen: seen.append(cat)
	return seen


def _style(path):
	wb = load_workbook(path)
	for ws in wb.worksheets:
		ws.freeze_panes = "A2"
		ws.auto_filter.ref = ws.dimensions
		for col in ws.columns:
			width = min(50, max(10, max(len(str(c.value or "")) for c in col) + 2))
			ws.column_dimensions[col[0].column_letter].width = width
	wb.save(path)


def _build_wide_table(db, config: dict, target_date_str: str, log_ref):
	"""从 SQLite 构建横向对比宽表：每产品一行，N列价格 + 近N日均价。"""
	if not db or not config.get("enabled", True):
		return None, None

	window_days = config.get("window_days", 3)
	exclude_invalid = config.get("exclude_invalid_records", True)

	total_dates = db.get_distinct_price_date_count()
	dates = db.get_latest_price_dates(window_days)
	if not dates:
		log_ref.warning("SQLite 无价格数据")
		return None, None

	dates_sorted = sorted(dates)
	log_ref.info("数据库价格日期数：%d，窗口日期：%s", total_dates, "、".join(dates_sorted))

	records = db.get_records_by_price_dates(dates_sorted, exclude_invalid=exclude_invalid)
	log_ref.info("查询到 %d 条记录", len(records))
	if not records:
		return None, None

	from .price_statistics import product_key, compute_rolling_average
	# 按产品分组
	groups = defaultdict(list)
	for r in records:
		groups[product_key(r)].append(r)

	# 构建宽表
	wide_rows = []
	full3 = two = one = zero = 0
	for key, grp in groups.items():
		r0 = grp[0]
		row = {
			"分类": r0.get("category", ""),
			"品名": r0.get("product_name", ""),
			"规格": r0.get("specification", ""),
			"单位": r0.get("unit", ""),
		}
		for d in dates_sorted:
			day_rows = [r for r in grp if str(r.get("price_date", "")) == d]
			label = f"{d[5:]}均价"
			if day_rows:
				row[label] = day_rows[0].get("average_price")
			else:
				row[label] = None

		avg, cnt = compute_rolling_average(grp, dates_sorted, include_warning=True, exclude_invalid=exclude_invalid)
		row["近三日均价"] = avg
		row["有效天数"] = cnt

		if cnt >= 3: full3 += 1
		elif cnt == 2: two += 1
		elif cnt == 1: one += 1
		else: zero += 1

		wide_rows.append(row)

	log_ref.info("产品数：%d，完整三日：%d，两日：%d，单日：%d，无效：%d",
	             len(wide_rows), full3, two, one, zero)

	return wide_rows, dates_sorted


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

	# ── CSV：纵向原始数据（每天一行） ──
	df_raw = pd.DataFrame(rows)
	for c in COLUMNS:
		if c not in df_raw.columns: df_raw[c] = None
	df_csv = df_raw[COLUMNS].sort_values(["category", "product_name", "price_date"])
	csv = csv_summary_dir / f"{stem}.csv"
	df_csv.to_csv(csv, index=False, encoding="utf-8-sig")

	# 每分类 CSV
	cats_in_data = _sorted_categories(df_raw)
	for cat in cats_in_data:
		cat_dir = out / _safe_csv_name(cat)
		cat_dir.mkdir(parents=True, exist_ok=True)
		df_raw[df_raw.category == cat][COLUMNS].to_csv(
			cat_dir / f"SMM锂电现货价格_{_safe_csv_name(cat)}_{target_date}.csv",
			index=False, encoding="utf-8-sig")

	# ── Excel：横向宽表（每产品一行，三列日期价格） ──
	wide_rows, window_dates = _build_wide_table(db, rolling_config or {}, str(target_date), log)

	xlsx = excel_dir / f"{stem}.xlsx"
	tmp = xlsx.with_suffix(".tmp.xlsx")

	if wide_rows:
		df_wide = pd.DataFrame(wide_rows)
		# 列顺序：分类 品名 规格 单位 | 日期列... | 近三日均价 有效天数
		date_cols = [c for c in df_wide.columns if "均价" in c and "近三日" not in c]
		other_cols = ["分类", "品名", "规格", "单位"]
		ordered = [c for c in other_cols if c in df_wide.columns] + date_cols + ["近三日均价", "有效天数"]
		df_wide = df_wide[[c for c in ordered if c in df_wide.columns]]
		df_wide = df_wide.sort_values(["分类", "品名"]).reset_index(drop=True)

		with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
			df_wide.to_excel(writer, index=False, sheet_name="全部数据")
			for cat in sorted(df_wide["分类"].unique()):
				df_wide[df_wide["分类"] == cat].to_excel(writer, index=False, sheet_name=_excel_sheet_name(cat))
			success_cats = meta.get("success_categories", [])
			window_str = "、".join(window_dates) if window_dates else ""
			pd.DataFrame({
				"项目": ["数据来源", "采集日期", "窗口日期", "成功分类", "产品数"],
				"内容": ["SMM", str(target_date), window_str,
				        f"{len(success_cats)}个分类成功", f"{len(wide_rows)}个产品"]
			}).to_excel(writer, index=False, sheet_name="采集说明")
		_style(tmp)
		os.replace(tmp, xlsx)
	else:
		# 无多日数据时用原始纵向数据
		with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
			df_raw[COLUMNS].to_excel(writer, index=False, sheet_name="全部数据")
			for cat in cats_in_data:
				df_raw[df_raw.category == cat][COLUMNS].to_excel(writer, index=False, sheet_name=_excel_sheet_name(cat))
			pd.DataFrame({
				"项目": ["数据来源", "采集日期", "成功分类", "失败分类"],
				"内容": ["SMM", str(target_date),
				        "、".join(meta.get("success_categories", [])),
				        "、".join(meta.get("failed_categories", []))]
			}).to_excel(writer, index=False, sheet_name="采集说明")
		_style(tmp)
		os.replace(tmp, xlsx)

	# ── 历史汇总 + 固定汇总（不变） ──
	if meta.get("status") == "success":
		history = export_root / "SMM锂电现货价格_历史汇总.xlsx"
		base_df = df_raw[COLUMNS].copy()
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
