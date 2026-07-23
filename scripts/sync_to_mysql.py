#!/usr/bin/env python
"""SQLite → MySQL 数据同步。

用法:
    python scripts/sync_to_mysql.py                              # 同步全部数据
    python scripts/sync_to_mysql.py --date 2026-07-23            # 同步指定日期
    python scripts/sync_to_mysql.py --start-date 2026-07-01 --end-date 2026-07-23  # 日期范围
    python scripts/sync_to_mysql.py --full                       # 全量重新同步
    python scripts/sync_to_mysql.py --dry-run                    # 预览不写入
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from smm_collector.logger import setup_logging
from smm_collector.synchronizer import sync, generate_quality_report


def main():
    p = argparse.ArgumentParser(description="SQLite → MySQL 数据同步")
    p.add_argument("--date", type=date.fromisoformat, help="同步指定日期 (YYYY-MM-DD)")
    p.add_argument("--start-date", type=date.fromisoformat, help="同步起始日期")
    p.add_argument("--end-date", type=date.fromisoformat, help="同步结束日期")
    p.add_argument("--full", action="store_true", help="全量重新同步")
    p.add_argument("--dry-run", action="store_true", help="预览模式，不实际写入")
    p.add_argument("--db", default=str(ROOT / "data/database/smm_lithium.db"),
                   help="SQLite 数据库路径")
    p.add_argument("--quality-report", action="store_true", help="额外输出数据质量报告 JSON")
    args = p.parse_args()

    log = setup_logging(ROOT)
    db_path = Path(args.db)
    if not db_path.exists():
        sys.exit(f"[ERROR] 数据库不存在: {db_path}")

    # 确定同步范围
    date_from, date_to = None, None
    sync_mode = "incremental"

    if args.date:
        date_from = args.date
        date_to = args.date
        sync_mode = "date_range"
    elif args.start_date or args.end_date:
        date_from = args.start_date
        date_to = args.end_date or date.today()
        sync_mode = "date_range"
    elif args.full:
        sync_mode = "full"

    print(f"Source : {db_path}")
    print(f"Target : MySQL {os.getenv('MYSQL_HOST','127.0.0.1')}:{os.getenv('MYSQL_PORT','3306')}/{os.getenv('MYSQL_DATABASE','smm_lithium')}")
    print(f"Mode   : {sync_mode}")
    if date_from:
        print(f"Date   : {date_from}" + (f" ~ {date_to}" if date_to and date_to != date_from else ""))
    if args.dry_run:
        print("[DRY RUN] 预览模式，不写入数据库\n")
    else:
        print()

    # 执行同步
    stats = sync(db_path, date_from, date_to, dry_run=args.dry_run)

    # 输出统计
    print(f"\n{'='*50}")
    print(f"MySQL 同步{'预览' if args.dry_run else '完成'}")
    print(f"批次     : {stats['batch_id']}")
    print(f"SQLite读取: {stats['source_count']}")
    print(f"有效数据  : {stats['source_count'] - stats['warning'] - stats['invalid']}")
    print(f"警告数据  : {stats['warning']}")
    print(f"无效数据  : {stats['invalid']}")
    if not args.dry_run:
        print(f"新增      : {stats['inserted']}")
        print(f"更新      : {stats['updated']}")
        print(f"跳过(重复): {stats['skipped']}")
        print(f"失败      : {stats['failed']}")
    print(f"异常问题  : {stats['quality_issues']}")
    print(f"状态      : {stats['status']}")
    if stats.get("error"):
        print(f"错误      : {stats['error']}")
    print(f"{'='*50}")

    # 数据质量报告
    if args.quality_report:
        report = generate_quality_report(stats)
        report_dir = ROOT / "data/exports" / date.today().strftime("%Y/%m")
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"SMM数据质量报告_{date.today()}.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n质量报告: {report_path}")

    if stats["status"] == "failed":
        sys.exit(1)
    elif stats["status"] == "partial_success":
        sys.exit(2)


if __name__ == "__main__":
    main()
