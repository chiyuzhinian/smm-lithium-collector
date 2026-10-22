"""SSL 证书到期检查（阿里云免费证书 90 天有效期，无自动续签）。

每天检查证书剩余有效期，剩余 ≤30/15/7 天时通过钉钉机器人发提醒；
同一到期日同一阈值只提醒一次（去重状态存 ops_events，与采集器共用 auth.db）。
证书更换后（新到期日）提醒自动重新武装。更换步骤见
docs/change-records/2026-10-22/price-domain-dingtalk-report/07-ssl-cert-ops-manual.md。

用法：
  .venv/bin/python scripts/ssl_cert_expiry_check.py      # 检查并按需发送提醒
  DINGTALK_DRY_RUN=1 ...                                 # 预览：只记日志，不发送不写去重
  SSL_CERT_PATH=/tmp/test.pem ...                        # 测试用：覆盖默认证书路径
退出码：0=正常（无需提醒或发送成功）；1=异常（证书读取失败/发送失败）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("smm_collector.ssl_expiry")

DEFAULT_CERT_PATH = "/etc/nginx/ssl/price.ldhs.online/fullchain.pem"
WARN_THRESHOLDS_DAYS = (30, 15, 7)
EVENT_TYPE = "SSL_CERT_EXPIRY_WARN"
DOMAIN = "price.ldhs.online"
MANUAL_REF = ("docs/change-records/2026-10-22/price-domain-dingtalk-report/"
              "07-ssl-cert-ops-manual.md")


def read_cert_not_after(cert_path: str | Path) -> datetime:
    """读取证书 notAfter（UTC）。失败抛异常。"""
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    with open(cert_path, "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read(), default_backend())
    return cert.not_valid_after_utc


def days_left(not_after: datetime, now: datetime | None = None) -> int:
    """剩余整天数（已过期/不足一天按 0 计）。"""
    now = now or datetime.now(timezone.utc)
    return max(0, (not_after - now).days)


def plan_notice(days: int, thresholds: tuple[int, ...] = WARN_THRESHOLDS_DAYS) -> str | None:
    """返回本次应发送的提醒 key（每次至多一个，最紧迫的适用阈值）；None=不提醒。

    key: "expired"（已到期）/ "d30" / "d15" / "d7"。
    去重按 (到期日, key) 记 ops_events——同一到期日每阈值只提醒一次。
    """
    if days <= 0:
        return "expired"
    for t in sorted(thresholds):
        if days <= t:
            return f"d{t}"
    return None


def notice_text(key: str, expiry: str, days: int) -> str:
    if key == "expired":
        return (f"⚠️ {DOMAIN} 的 SSL 证书已于 {expiry} 到期，HTTPS 访问将出现证书错误，请立即更换。\n"
                f"更换步骤：{MANUAL_REF}")
    return (f"⏰ {DOMAIN} 的 SSL 证书将于 {expiry} 到期（剩余 {days} 天）。\n"
            f"免费证书无自动续签，请提前到阿里云控制台重新申请并部署。\n"
            f"更换步骤：{MANUAL_REF}")


def main() -> int:
    # 先加载 .env（DINGTALK_*），再导入 notifier（webhook 在模块导入时读取）
    from smm_collector.config import load_config
    load_config()
    from smm_collector import notifier, ops_events

    cert_path = os.getenv("SSL_CERT_PATH", DEFAULT_CERT_PATH)
    try:
        not_after = read_cert_not_after(cert_path)
    except Exception:
        log.exception("证书读取失败: %s", cert_path)
        return 1
    days = days_left(not_after)
    expiry = not_after.date().isoformat()
    log.info("证书检查: %s 到期=%s 剩余=%d 天", cert_path, expiry, days)

    key = plan_notice(days)
    if key is None:
        log.info("未到提醒阈值（≥31 天），不发送")
        return 0
    mark = f"cert={expiry} key={key}"
    if ops_events.has_event(EVENT_TYPE, mark):
        log.info("该阈值已提醒过，跳过: %s", mark)
        return 0
    ok = asyncio.run(notifier.send_dingtalk("SSL 证书到期提醒", notice_text(key, expiry, days)))
    if not ok:
        log.error("提醒发送失败: %s", mark)
        return 1
    if not os.getenv("DINGTALK_DRY_RUN"):  # 预览不写去重，真实发送后记录
        ops_events.write_event(EVENT_TYPE, "info", f"{mark} days={days}")
    log.info("已发送提醒: %s", mark)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
