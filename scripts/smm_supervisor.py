"""scripts/smm_supervisor.py — Supervisor CLI 入口（Phase E1）。

替代 `scripts/run_daily.py` 被 cron 调用；保持 run_daily.sh 包装不变。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 让 scripts/ 能 import src/smm_collector/
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smm_collector.supervisor import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())