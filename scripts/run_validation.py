import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from datetime import date
import argparse

from smm_collector.daily_validation import run_daily_validation


def cli():
    p = argparse.ArgumentParser(description="SMM 每日数据验证（后台 9 层检查，结果写 logs/validation/）")
    p.add_argument("--date", type=date.fromisoformat, default=date.today(),
                   help="业务日期（YYYY-MM-DD），默认今天")
    args = p.parse_args()
    res = run_daily_validation(args.date.isoformat())
    print(f"date={res['date']} verdict={res['verdict']} "
          f"errors={res['counts']['error']} warnings={res['counts']['warning']}")
    print(f"log={res['log_file']}")
    for i in res["issues"]:
        if i["level"] != "info":
            print(f"  [{i['level']}] {i['layer']}: {i['message']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
