"""从 SQLite 全量重建历史汇总与固定汇总（不依赖错误的汇总文件层层叠加）。

用法:
  .venv/bin/python scripts/rebuild_summaries.py                # 重建历史汇总 + 正式固定汇总
  .venv/bin/python scripts/rebuild_summaries.py --dry-run      # 只打印统计与门控复核，不写文件
  .venv/bin/python scripts/rebuild_summaries.py --fix-gaps     # 并补生成缺口日的每日导出 + 状态 manifest
  .venv/bin/python scripts/rebuild_summaries.py --from 2026-07-01 --to 2026-08-12  # 限定日期范围

设计要点:
  - 数据源 = SQLite（唯一可靠全量源），剔除 invalid 行 + 质量感知去重（valid>warning、collected_at 新优先）
  - 正式固定汇总 = 全部数据 + 每分类 Sheet + 采集说明
  - 按日门控复核：逐日期输出「该日若当时采集是否满足门控」，不达标日期在报告中标注
  - --fix-gaps 对「DB 有行但无每日导出文件」的日期补生成 CSV/XLSX/分类 CSV 与 manifest
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from smm_collector.database import Database
from smm_collector.config import load_config, load_portal_config
from smm_collector.exporter import (
    export_daily, build_summary_dataframe, _write_fixed_summary, _style,
)


def _gate_check(day_rows: list[dict], canonical: list[str], gate: dict, target: str,
                run_cats: list[str] | None = None) -> dict:
    """对某日数据行做门控复核（与 exporter.update_summaries 同规则，但不写文件）。

    缺失分类优先取 collection_runs 里该日运行的 success_categories（采集层真实结果，
    不受周更品种行日期滞后的影响）；无运行记录时才退回用行内出现的分类估算。
    """
    n = len(day_rows)
    if n == 0:
        return {"eligible": False, "missing_categories": sorted(canonical),
                "reasons": ["无数据行"]}
    if run_cats:
        missing = sorted(set(canonical) - set(run_cats))
    else:
        cats = sorted({r.get("category", "") for r in day_rows})
        missing = sorted(set(canonical) - set(cats))
    invalid_n = sum(1 for r in day_rows if r.get("validation_status") == "invalid")
    align_n = sum(1 for r in day_rows if str(r.get("price_date", "")) == target)
    invalid_ratio = invalid_n / n
    align_ratio = align_n / n
    reasons = []
    if missing:
        head = "、".join(missing[:8]) + ("…" if len(missing) > 8 else "")
        reasons.append(f"缺失分类 {len(missing)} 个: {head}")
    min_align = float(gate.get("min_date_alignment_ratio", 0.6))
    max_invalid = float(gate.get("max_invalid_ratio", 0.05))
    if align_ratio < min_align:
        reasons.append(f"日期对齐率 {align_ratio:.1%} 低于阈值 {min_align:.0%}")
    if invalid_ratio > max_invalid:
        reasons.append(f"invalid 行占比 {invalid_ratio:.1%} 超过阈值 {max_invalid:.0%}")
    eligible = (not missing) and align_ratio >= min_align and invalid_ratio <= max_invalid
    cats_out = sorted({r.get("category", "") for r in day_rows})
    return {"eligible": eligible, "missing_categories": missing, "reasons": reasons,
            "align_ratio": round(align_ratio, 4), "invalid_ratio": round(invalid_ratio, 4),
            "categories": cats_out}


def main():
    ap = argparse.ArgumentParser(description="从 SQLite 重建历史汇总与固定汇总")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写任何文件")
    ap.add_argument("--fix-gaps", action="store_true", help="补生成缺口日的每日导出与 manifest")
    ap.add_argument("--from", dest="date_from", help="起始日期 YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="结束日期 YYYY-MM-DD")
    args = ap.parse_args()

    cfg = load_config(ROOT)
    portal = load_portal_config(ROOT)
    canonical = list(portal.get("canonical_categories") or [])
    gate = portal.get("summary_gate") or {}
    export_root = cfg.path("export_dir")
    db = Database(cfg.path("database_path"))

    rows = db.get_all_records()
    if args.date_from:
        rows = [r for r in rows if str(r.get("price_date", "")) >= args.date_from]
    if args.date_to:
        rows = [r for r in rows if str(r.get("price_date", "")) <= args.date_to]
    print(f"SQLite 行数: {len(rows)}")

    kept = [r for r in rows if r.get("validation_status") != "invalid"]
    print(f"剔除 invalid: {len(rows) - len(kept)} 行 → 剩余 {len(kept)}")
    df = build_summary_dataframe(kept)
    print(f"质量感知去重后: {len(df)} 行")

    if not args.dry_run:
        # 历史汇总
        history = export_root / "SMM锂电现货价格_历史汇总.xlsx"
        htmp = history.with_suffix(".tmp.xlsx")
        df.to_excel(htmp, index=False)
        _style(htmp)
        htmp.replace(history)
        print(f"[写] 历史汇总: {history} ({len(df)} 行)")

        # 正式固定汇总
        fixed_dir = export_root / "固定汇总"
        fixed_dir.mkdir(parents=True, exist_ok=True)
        fixed = fixed_dir / "SMM锂电现货价格_固定汇总.xlsx"
        max_date = max(str(r.get("price_date", "")) for r in kept) if kept else str(date.today())
        _write_fixed_summary(df, max_date, fixed,
                             f"由 rebuild_summaries.py 全量重建（{len(df)} 行）")
        print(f"[写] 正式固定汇总: {fixed}")

    # 按日门控复核
    by_date = defaultdict(list)
    for r in kept:
        by_date[str(r.get("price_date", ""))].append(r)
    # collection_runs 里各 target_date 的最佳运行成功分类（采集层真实结果）
    run_cats_by_date: dict[str, list[str]] = {}
    try:
        import sqlite3
        con = sqlite3.connect(cfg.path("database_path"))
        for run_id, target_date, status, succ in con.execute(
                "SELECT run_id, target_date, status, success_categories FROM collection_runs"):
            try:
                cats = json.loads(succ or "[]")
            except Exception:
                cats = []
            if not target_date:
                continue
            best = run_cats_by_date.get(str(target_date))
            if best is None or len(cats) > len(best):
                run_cats_by_date[str(target_date)] = cats
        con.close()
    except Exception as e:
        print(f"[WARN] 读取 collection_runs 失败: {e}")
    print("\n=== 按日门控复核（canonical %d 分类） ===" % len(canonical))
    n_ok = n_bad = 0
    gate_results = {}
    for d in sorted(by_date):
        res = _gate_check(by_date[d], canonical, gate, d,
                          run_cats=run_cats_by_date.get(d))
        gate_results[d] = res
        mark = "✅" if res["eligible"] else "❌"
        if res["eligible"]:
            n_ok += 1
        else:
            n_bad += 1
        reason = res["reasons"][0] if res["reasons"] else ""
        print(f"  {mark} {d}: {len(by_date[d]):>4} 行 | {reason}")

    # 缺口日清单（仅补门户历史覆盖范围 2026-07 起；更早的零散日不补）
    gap_dates = []
    for d in sorted(by_date):
        if d < "2026-07-01":
            continue
        csv = export_root / d[:4] / d[5:7] / "每日汇总" / "CSV" / f"SMM锂电现货价格_{d}.csv"
        if not csv.exists():
            gap_dates.append(d)
    print(f"\n缺口日（DB 有行但无每日导出）: {len(gap_dates)} 个")
    for d in gap_dates:
        print(f"  - {d} ({len(by_date[d])} 行)")

    def build_meta(d, day_rows, res):
        success_cats = run_cats_by_date.get(d) or res["categories"]
        return {
            "run_id": f"rebuild_{d}",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "target_date": d,
            "expected_categories": canonical,
            "success_categories": success_cats,
            "failed_categories": [],
            "status": "partial_success",  # 避免 export_daily 触发汇总增量更新（汇总已全量重建）
            "total_raw_rows": len(day_rows),
            "total_clean_rows": len(day_rows),
        }

    def build_decision(d, res):
        return {
            "decision": "updated_formal" if res["eligible"] else "temp_snapshot",
            "eligible": res["eligible"],
            "missing_categories": res["missing_categories"],
            "extra_categories": [],
            "align_ratio": res.get("align_ratio"),
            "invalid_ratio": res.get("invalid_ratio"),
            "reasons": res["reasons"] + (["由 rebuild_summaries.py 回补"] if res["eligible"] else ["由 rebuild_summaries.py 回补（非完整日）"]),
            "formal_status": "完整" if res["eligible"] else "部分",
            "temp_snapshot_path": None,
        }

    if args.fix_gaps:
        if args.dry_run:
            print("[dry-run] 跳过缺口日补生成与 manifest 刷新")
        else:
            # manifest 刷新清单：有 manifest 但决策与当前复核不一致的日期
            from smm_collector.data_quality import generate_status_manifest
            refresh_dates = []
            for d in sorted(by_date):
                if d < "2026-07-01":
                    continue
                man_path = export_root / d[:4] / d[5:7] / "每日汇总" / f"SMM数据状态_{d}.json"
                if not man_path.exists():
                    continue
                try:
                    old = json.loads(man_path.read_text(encoding="utf-8"))
                    old_eligible = (old.get("fixed_summary") or {}).get("eligible")
                    old_rebuild = "rebuild_summaries.py" in json.dumps(old.get("fixed_summary") or {}, ensure_ascii=False)
                except Exception:
                    old_eligible, old_rebuild = None, False
                res = gate_results[d]
                # 只刷新 rebuild 自己写过的 manifest；真实采集写的 manifest 是权威的，不覆盖
                if old_rebuild and old_eligible != res["eligible"]:
                    refresh_dates.append(d)
            print(f"\nmanifest 需刷新: {len(refresh_dates)} 个日期")
            for d in refresh_dates:
                try:
                    res = gate_results[d]
                    generate_status_manifest(build_meta(d, by_date[d], res), by_date[d],
                                             build_decision(d, res), export_root)
                    print(f"  [写] {d} 状态 manifest（决策={build_decision(d, res)['decision']}）")
                except Exception as e:
                    print(f"  [manifest失败] {d}: {e}")

            if gap_dates:
                print("\n=== 补生成缺口日导出 ===")
                for d in gap_dates:
                    day_rows = by_date[d]
                    res = gate_results[d]
                    meta = build_meta(d, day_rows, res)
                    try:
                        xlsx, csv = export_daily(day_rows, meta, export_root,
                                                 date.fromisoformat(d), db=db)
                        print(f"  [写] {d}: {xlsx.name} ({len(day_rows)} 行)")
                    except Exception as e:
                        print(f"  [失败] {d}: {e}")
                        continue
                    try:
                        generate_status_manifest(meta, day_rows, build_decision(d, res), export_root)
                        print(f"  [写] {d} 状态 manifest")
                    except Exception as e:
                        print(f"  [manifest失败] {d}: {e}")

    print(f"\n完成。门控达标日 {n_ok} 个 / 不达标日 {n_bad} 个。")


if __name__ == "__main__":
    main()
