"""数据质量报告生成 + 每日数据状态 manifest。"""
from __future__ import annotations
import json
from datetime import date, datetime
from pathlib import Path


def generate_daily_report(meta: dict, sync_stats: dict | None = None,
                          output_dir: Path | None = None) -> dict:
	category_counts = meta.get("category_counts", {})
	total_raw = meta.get("total_raw_rows", 0)
	total_clean = meta.get("total_clean_rows", 0)
	abnormal = sum(c.get("abnormal", 0) for c in category_counts.values())

	report = {
		"generated_at": datetime.now().isoformat(timespec="seconds"),
		"target_date": meta.get("target_date", ""),
		"collection_status": meta.get("status", "unknown"),
		"collection": {
			"total_raw_rows": total_raw,
			"total_clean_rows": total_clean,
			"abnormal_count": abnormal,
			"valid_count": total_clean - abnormal,
			"categories_expected": len(meta.get("expected_categories", [])),
			"categories_succeeded": len(meta.get("success_categories", [])),
			"categories_failed": len(meta.get("failed_categories", [])),
		},
		"sync": None,
	}
	if sync_stats:
		report["sync"] = {
			"batch_id": sync_stats.get("batch_id", ""),
			"status": sync_stats.get("status", "unknown"),
			"inserted": sync_stats.get("inserted", 0),
			"updated": sync_stats.get("updated", 0),
			"skipped": sync_stats.get("skipped", 0),
			"failed": sync_stats.get("failed", 0),
			"warnings": sync_stats.get("warning", 0),
			"invalids": sync_stats.get("invalid", 0),
			"quality_issues": sync_stats.get("quality_issues", 0),
		}
	if output_dir:
		target = meta.get("target_date", date.today().isoformat())
		out = output_dir / f"{target[:4]}" / f"{target[5:7]}" / "每日汇总" / "Excel"
		out.mkdir(parents=True, exist_ok=True)
		path = out / f"SMM数据质量报告_{target}.json"
		path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
	return report


def generate_status_manifest(meta: dict, rows: list[dict], summary_decision: dict | None,
                             export_root: Path | None = None) -> dict:
	"""每日数据状态 manifest：无条件生成（与 MySQL 同步开关无关）。

	写到 {export_root}/{Y}/{M}/每日汇总/SMM数据状态_{target_date}.json。
	字段覆盖：日期、预期/成功/失败分类数、缺失分类名单、行级质量统计、
	日期对齐率、当日全部 runs、固定汇总决策与状态、生成时间。
	"""
	target = str(meta.get("target_date") or date.today().isoformat())
	decision = summary_decision or {}
	n = len(rows)
	valid = sum(1 for r in rows if r.get("validation_status") == "valid")
	invalid = sum(1 for r in rows if r.get("validation_status") == "invalid")
	warning = n - valid - invalid
	align = sum(1 for r in rows if str(r.get("price_date", "")) == target)

	formal_updated_at = None
	if export_root:
		fixed_file = export_root / "固定汇总" / "SMM锂电现货价格_固定汇总.xlsx"
		if fixed_file.exists():
			formal_updated_at = datetime.fromtimestamp(fixed_file.stat().st_mtime).isoformat(timespec="seconds")

	manifest = {
		"generated_at": datetime.now().isoformat(timespec="seconds"),
		"target_date": target,
		"collection": {
			"status": meta.get("status", "unknown"),
			"categories_expected": len(meta.get("expected_categories", [])),
			"categories_succeeded": len(meta.get("success_categories", [])),
			"categories_failed": len(meta.get("failed_categories", [])),
			"failed_category_names": sorted(meta.get("failed_categories", [])),
			"missing_categories": decision.get("missing_categories", []),
			"extra_categories": decision.get("extra_categories", []),
			"total_raw_rows": meta.get("total_raw_rows", 0),
			"total_clean_rows": n,
			"valid_count": valid,
			"warning_count": warning,
			"invalid_count": invalid,
			"valid_ratio": round(valid / n, 4) if n else None,
			"invalid_ratio": round(invalid / n, 4) if n else None,
			"date_alignment_ratio": round(align / n, 4) if n else None,
		},
		"quality_report": None,
		"fixed_summary": {
			"decision": decision.get("decision", "unknown"),
			"eligible": bool(decision.get("eligible", False)),
			"formal_status": decision.get("formal_status"),
			"formal_updated_at": formal_updated_at,
			"formal_file_path": "固定汇总/SMM锂电现货价格_固定汇总.xlsx",
			"temp_snapshot_path": decision.get("temp_snapshot_path"),
			"reasons": decision.get("reasons", []),
		},
	}

	if export_root:
		target_y, target_m = target[:4], target[5:7]
		qr = export_root / target_y / target_m / "每日汇总" / "Excel" / f"SMM数据质量报告_{target}.json"
		manifest["quality_report"] = {
			"path": f"{target_y}/{target_m}/每日汇总/Excel/SMM数据质量报告_{target}.json",
			"exists": qr.exists(),
		}

		out_dir = export_root / target_y / target_m / "每日汇总"
		out_dir.mkdir(parents=True, exist_ok=True)
		path = out_dir / f"SMM数据状态_{target}.json"

		# 同一天多次运行时保留历史 run 记录（合并已有 manifest）
		runs = [{"run_id": meta.get("run_id", ""),
		         "started_at": meta.get("started_at", ""),
		         "finished_at": meta.get("finished_at", ""),
		         "status": meta.get("status", "unknown")}]
		if path.exists():
			try:
				old = json.loads(path.read_text(encoding="utf-8"))
				old_runs = old.get("runs", [])
				known = {r.get("run_id") for r in old_runs}
				runs = old_runs + [r for r in runs if r["run_id"] and r["run_id"] not in known]
			except Exception:
				pass
		manifest["runs"] = runs[-20:]
		path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
	return manifest
