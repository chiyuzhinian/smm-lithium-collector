"""华友月度业务报表 CLI：按月份从 SQLite 生成报表 Excel（主表+明细+计算说明）。

用法：
  .venv/bin/python scripts/build_monthly_report.py --month 2026-08
  .venv/bin/python scripts/build_monthly_report.py --month 2026-09   # 未结束月份=期间预览
输出：data/exports/月度业务报表/华友月度业务报表_{month}.xlsx（不覆盖原始模板/历史文件）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from smm_collector import portal_service as ps            # noqa: E402
from smm_collector import monthly_report as mr            # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="生成华友月度业务报表（依据数据库采集报价）")
    ap.add_argument("--month", required=True, help="月份 YYYY-MM（未结束月份生成期间预览）")
    args = ap.parse_args()

    products, meta = ps.load_products(ROOT / "config")
    if meta["load_errors"]:
        print("映射配置错误:", *meta["load_errors"], sep="\n  - ")
        return 2
    if ps.month_bounds(args.month) is None:
        print(f"月份格式非法: {args.month}（应为 YYYY-MM）")
        return 2

    con = ps.open_readonly(ROOT / "data" / "database" / "smm_lithium.db")
    try:
        payload = ps.monthly_payload(products, con, args.month, meta)
    finally:
        con.close()
    s = payload["summary"]
    print(f"{args.month}: {s['total_rows']} 行（SMM {s['smm_rows']}）"
          f"｜月均价填充 {s['filled']} 行、留空 {s['blank']} 行"
          f"｜环比计算 {s['mom_computed']} 行、留空并说明 {s['mom_blocked']} 行"
          + ("" if s["month_ended"] else "｜⚠ 月份未结束，为期间均价"))

    out_dir = ROOT / "data" / "exports" / "月度业务报表"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / mr.report_filename(args.month)
    buf = mr.build_monthly_report_xlsx(products, con, args.month, meta)
    out.write_bytes(buf.getvalue())
    print(f"已生成: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
