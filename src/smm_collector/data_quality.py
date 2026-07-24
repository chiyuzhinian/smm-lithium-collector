"""数据质量报告生成。"""
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
		out = output_dir / f"{target[:4]}" / f"{target[5:7]}" / "每日汇总"
		out.mkdir(parents=True, exist_ok=True)
		path = out / f"SMM数据质量报告_{target}.json"
		path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
	return report
