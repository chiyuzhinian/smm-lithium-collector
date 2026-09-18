"""scripts/smm_health.py — 统一健康检查（Phase E2）。"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smm_collector.auth_status import DEFAULT_PATH as AUTH_PATH
from smm_collector.auth_status import read as read_auth_status
from smm_collector.collector_status import DEFAULT_PATH as COLLECTOR_PATH
from smm_collector.collector_status import read as read_collector_status
from smm_collector.config import load_config
from smm_collector.staleness import StalenessConfig, check_staleness


def main() -> int:
    cfg = load_config()
    auth = read_auth_status(AUTH_PATH)
    coll = read_collector_status(COLLECTOR_PATH)
    staleness_cfg = StalenessConfig.from_settings(cfg.settings if cfg else {})
    staleness = check_staleness(coll, now=datetime.now(), cfg=staleness_cfg)

    payload = {
        "auth": auth,
        "collector": coll,
        "staleness": {
            "status": staleness.status,
            "detail": staleness.detail,
            "next_action": staleness.next_action,
            "expected_business_date": staleness.expected_business_date,
            "consecutive_failures": staleness.consecutive_failures,
        },
        "auth_status_path": str(AUTH_PATH),
        "collector_status_path": str(COLLECTOR_PATH),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }

    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))

    summary = (
        f"auth={auth.get('status')} (reason={auth.get('reason')})\n"
        f"collector={coll.get('status')} last_success={coll.get('last_success_at')} "
        f"data_date={coll.get('latest_price_date') or coll.get('data_date')} "
        f"consecutive_failures={coll.get('consecutive_failures')}\n"
        f"staleness={staleness.status} ({staleness.detail})\n"
        f"next_action: {staleness.next_action}"
    )
    print("\n--- Summary ---\n" + summary, file=sys.stderr)

    if staleness.status == "crit":
        return 2
    if staleness.status == "warn" or auth.get("status") not in ("AUTH_OK", "AUTH_UNKNOWN"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())