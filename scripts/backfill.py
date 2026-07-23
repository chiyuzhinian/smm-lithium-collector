import argparse,asyncio,sys
from datetime import date,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/"src"))
from smm_collector.config import load_config
from smm_collector.main import collect
async def main():
 p=argparse.ArgumentParser(); p.add_argument("--start-date",required=True,type=date.fromisoformat); p.add_argument("--end-date",required=True,type=date.fromisoformat); p.add_argument("--headed",action="store_true"); a=p.parse_args()
 cfg=load_config(ROOT)
 if not cfg.selectors.get("date_control"): print("目标页面当前不支持该日期补采。"); return 3
 day=a.start_date; code=0
 while day<=a.end_date:
  # Date-control behavior must be confirmed by inspection before implementation.
  print(f"{day}: 目标页面当前不支持该日期补采。"); code=3; day+=timedelta(days=1)
 return code
if __name__=="__main__": raise SystemExit(asyncio.run(main()))

