"""每日Excel/CSV导出 + 规范日报。"""
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


SUMMARY_KEYS = ["source", "market", "category", "product_name", "specification", "unit", "price_date"]
_STATUS_RANK = {"valid": 0, "warning": 1, "invalid": 2}


def _normalize_merge_inputs(df: pd.DataFrame) -> pd.DataFrame:
	"""Excel 往返会改变数据类型，导致增量合并时去重键无法碰撞
	（历史/固定汇总重复膨胀）。归一化两类差异：
	  - price_date：datetime 对象 → ISO 日期字符串（date 对象同值不同型）
	  - 空字符串 → NaN（字符串键列）"""
	if df.empty:
		return df
	df = df.copy()
	df["price_date"] = (pd.to_datetime(df["price_date"], errors="coerce")
	                    .dt.strftime("%Y-%m-%d").fillna(""))
	for c in ("source", "market", "category", "product_name", "specification", "unit"):
		if c in df.columns:
			df[c] = df[c].fillna("")
	return df


def _dedup_quality(rows: list[dict]) -> list[dict]:
	"""质量感知去重：同业务键多行时优先 valid，其次 warning，同级取 collected_at 最新。

	修复历史汇总/固定汇总 keep="last" 导致坏数据覆盖好数据的问题。
	"""
	if not rows:
		return []
	df = pd.DataFrame(rows)
	for c in ("collected_at", "validation_status"):
		if c not in df.columns:
			df[c] = None
	df["_rank"] = df["validation_status"].map(lambda s: _STATUS_RANK.get(s, 2))
	df["_ts"] = pd.to_datetime(df["collected_at"], errors="coerce")
	keys = [k for k in SUMMARY_KEYS if k in df.columns]
	df = (df.sort_values(["_rank", "_ts"], ascending=[True, False])
	        .drop_duplicates(subset=keys, keep="first")
	        .drop(columns=["_rank", "_ts"]))
	return df.to_dict("records")


def build_summary_dataframe(db_rows: list[dict]) -> pd.DataFrame:
	"""过滤 invalid 行 + 质量感知去重，供增量更新与重建脚本共用。"""
	kept = [r for r in db_rows if r.get("validation_status") != "invalid"]
	kept = _dedup_quality(kept)
	df = pd.DataFrame(kept)
	for c in COLUMNS:
		if c not in df.columns:
			df[c] = None
	return df[COLUMNS].sort_values(["category", "product_name", "price_date"])


def _write_fixed_summary(merged: pd.DataFrame, target_date, path: Path, status_text: str):
	"""写固定汇总（正式或临时快照）：全部数据 + 每分类 Sheet + 采集说明。"""
	tmp = path.with_suffix(".tmp.xlsx")
	mc = _sorted_cats(merged["category"])
	cs = "、".join(mc[:5]) + ("…等" if len(mc) > 5 else "")
	with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
		merged.to_excel(writer, index=False, sheet_name="全部数据")
		for cat in mc:
			merged[merged.category == cat].to_excel(writer, index=False, sheet_name=_excel_sheet_name(cat))
		pd.DataFrame({"项目": ["数据来源", "最后更新日期", "数据范围", "采集状态"],
		              "内容": ["SMM", str(target_date), cs, status_text]}
		).to_excel(writer, index=False, sheet_name="采集说明")
	_style(tmp)
	os.replace(tmp, path)


def update_summaries(rows, meta, export_root: Path, target_date,
                     canonical_categories=None, gate=None) -> dict:
	"""固定汇总门控 + 历史汇总/固定汇总更新（含临时快照）。

	门控规则（canonical_categories 提供时）：
	  - missing = canonical - success：对照规范分类全集，而非当日 discover 集合
	  - 日期对齐率 = price_date==target_date 行占比（同日采集→valid，次日早采→warning）
	  - 达标 → 质量去重后更新历史汇总 + 正式固定汇总（decision=updated_formal）
	  - 不达标但有行 → 写 固定汇总/临时快照/，不动正式文件（decision=temp_snapshot）
	  - 无行 → skipped
	canonical_categories 为 None 时兼容旧调用方（按 meta.status 走旧门，质量去重仍生效）。
	"""
	canonical = list(canonical_categories) if canonical_categories is not None else None
	gate = gate or {}
	min_align = float(gate.get("min_date_alignment_ratio", 0.8))
	max_invalid = float(gate.get("max_invalid_ratio", 0.05))

	success_set = set(meta.get("success_categories", []))
	n = len(rows)
	align_ratio = invalid_ratio = None
	reasons: list[str] = []

	if canonical is None:
		# 旧调用方兼容：按 meta.status 判定
		if meta.get("status") != "success":
			return {"decision": "skipped", "eligible": False, "missing_categories": [],
			        "extra_categories": [], "align_ratio": None, "invalid_ratio": None,
			        "reasons": ["status != success（旧门控）"], "formal_status": "异常",
			        "temp_snapshot_path": None}
		missing: list[str] = []
		extra: list[str] = []
	else:
		missing = sorted(set(canonical) - success_set)
		extra = sorted(success_set - set(canonical))
		if missing:
			head = "、".join(missing[:8]) + ("…" if len(missing) > 8 else "")
			reasons.append(f"缺失分类 {len(missing)} 个: {head}")

	if n:
		invalid_n = sum(1 for r in rows if r.get("validation_status") == "invalid")
		align_n = sum(1 for r in rows if str(r.get("price_date", "")) == str(target_date))
		invalid_ratio = invalid_n / n
		align_ratio = align_n / n
		if align_ratio < min_align:
			reasons.append(f"日期对齐率 {align_ratio:.1%} 低于阈值 {min_align:.0%}")
		if invalid_ratio > max_invalid:
			reasons.append(f"invalid 行占比 {invalid_ratio:.1%} 超过阈值 {max_invalid:.0%}")

	if n == 0:
		return {"decision": "skipped", "eligible": False, "missing_categories": missing,
		        "extra_categories": extra, "align_ratio": None, "invalid_ratio": None,
		        "reasons": reasons or ["无数据行"], "formal_status": "异常",
		        "temp_snapshot_path": None}

	eligible = (not missing) and align_ratio >= min_align and invalid_ratio <= max_invalid
	decision = "updated_formal" if eligible else "temp_snapshot"
	result = {
		"decision": decision,
		"eligible": eligible,
		"missing_categories": missing,
		"extra_categories": extra,
		"align_ratio": round(align_ratio, 4),
		"invalid_ratio": round(invalid_ratio, 4),
		"reasons": reasons,
		"formal_status": None,
		"temp_snapshot_path": None,
	}

	if decision == "updated_formal":
		# 历史汇总：增量合并 + 全量质量感知去重
		history = export_root / "SMM锂电现货价格_历史汇总.xlsx"
		base_df = build_summary_dataframe(rows)
		old = pd.read_excel(history) if history.exists() else pd.DataFrame(columns=COLUMNS)
		old, base_df = _normalize_merge_inputs(old), _normalize_merge_inputs(base_df)
		merged = _dedup_quality(pd.concat([old, base_df], ignore_index=True).to_dict("records"))
		merged = pd.DataFrame(merged)[COLUMNS]
		htmp = history.with_suffix(".tmp.xlsx")
		merged.to_excel(htmp, index=False)
		_style(htmp)
		os.replace(htmp, history)
		# 正式固定汇总
		fixed_dir = export_root / "固定汇总"
		fixed_dir.mkdir(parents=True, exist_ok=True)
		fixed = fixed_dir / "SMM锂电现货价格_固定汇总.xlsx"
		_write_fixed_summary(merged, target_date, fixed, f"共{len(success_set)}个分类完整成功")
		result["formal_status"] = "完整"
		result["formal_file_path"] = "固定汇总/SMM锂电现货价格_固定汇总.xlsx"
	else:
		# 临时快照：不覆盖正式固定汇总
		snap_dir = export_root / "固定汇总" / "临时快照"
		snap_dir.mkdir(parents=True, exist_ok=True)
		snap = snap_dir / f"SMM锂电现货价格_固定汇总_临时_{target_date}.xlsx"
		_write_fixed_summary(build_summary_dataframe(rows), target_date, snap,
		                     f"临时快照（成功 {len(success_set)}/{len(canonical) if canonical else '?'} 分类，仅供参考）")
		result["formal_status"] = "部分" if success_set else "异常"
		result["temp_snapshot_path"] = f"固定汇总/临时快照/{snap.name}"
	return result


def export_daily(rows, meta, export_root: Path, target_date, db=None, data_date=None):
	"""每日导出。文件命名按运行日 target_date；门控/规范日报/固定汇总按数据日期 data_date。"""
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
		pd.DataFrame({"项目":["数据来源","采集日期","数据日期","成功分类","数据行数"],
			"内容":["SMM",str(target_date),str(data_date or target_date),
			       f"{len(meta.get('success_categories',[]))}个分类",f"{len(rows)}条"]}
		).to_excel(writer, index=False, sheet_name="采集说明")
	_style(daily_tmp); os.replace(daily_tmp, daily_xlsx)

	# 规范日报
	try:
		from .business_report import build_report, write_report_sheet
		all_smm = db.get_all_records() if db else rows
		report_df, quality = build_report(all_smm, target_date=(data_date or target_date))
		report_xlsx = excel_dir / f"SMM锂电现货价格_规范日报_{target_date}.xlsx"
		with pd.ExcelWriter(report_xlsx, engine="openpyxl") as rw:
			write_report_sheet(rw, report_df)
		log.info("规范日报: %d/%d matched", quality["matched"], quality["total_required"])
	except Exception:
		log.exception("规范日报生成失败")

	# 历史汇总 + 固定汇总（门控决策；canonical/gate 由 meta 传入，缺省走旧门兼容）
	meta["summary_decision"] = update_summaries(
		rows, meta, export_root, data_date or target_date,
		canonical_categories=meta.get("canonical_categories"),
		gate=meta.get("summary_gate"))

	# OneDrive
	onedrive = os.getenv("ONEDRIVE_EXPORT_DIR","")
	if onedrive:
		try:
			import shutil; od = Path(onedrive)/f"{target_date:%Y}"/f"{target_date:%m}"; od.mkdir(parents=True,exist_ok=True)
			shutil.copy2(daily_xlsx, od/daily_xlsx.name)
		except Exception: pass

	return daily_xlsx, csv
