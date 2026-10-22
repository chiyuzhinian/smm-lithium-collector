"""SSL 证书到期检查纯逻辑测试（days_left / plan_notice / 文案）。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ssl_cert_expiry_check import days_left, plan_notice, notice_text  # noqa: E402


def _utc():
    return datetime(2026, 10, 22, 8, 0, 0, tzinfo=timezone.utc)


def test_days_left_rounding():
    now = _utc()
    assert days_left(now + timedelta(days=3, hours=12), now=now) == 3
    assert days_left(now + timedelta(days=1), now=now) == 1
    assert days_left(now + timedelta(hours=23), now=now) == 0  # 不足一天按 0
    assert days_left(now - timedelta(days=2), now=now) == 0  # 已过期按 0


def test_plan_notice_ranges():
    assert plan_notice(88) is None      # 远未到期
    assert plan_notice(31) is None      # 刚过阈值线不提醒
    assert plan_notice(30) == "d30"
    assert plan_notice(20) == "d30"     # 晚装场景：补发 30 天档（文案带实际剩余天数）
    assert plan_notice(15) == "d15"
    assert plan_notice(8) == "d15"
    assert plan_notice(7) == "d7"
    assert plan_notice(3) == "d7"
    assert plan_notice(0) == "expired"
    assert plan_notice(-1) == "expired"


def test_plan_notice_each_run_at_most_one():
    # 每次至多返回一个 key（最紧迫的适用阈值），避免一次刷多条
    assert plan_notice(5) == "d7"
    assert plan_notice(14) == "d15"


def test_notice_text_mentions_expiry_and_domain():
    t30 = notice_text("d30", "2027-01-19", 30)
    assert "price.ldhs.online" in t30
    assert "2027-01-19" in t30
    assert "30 天" in t30
    assert "免费证书无自动续签" in t30
    assert "07-ssl-cert-ops-manual.md" in t30
    t0 = notice_text("expired", "2027-01-19", 0)
    assert "已于 2027-01-19 到期" in t0
