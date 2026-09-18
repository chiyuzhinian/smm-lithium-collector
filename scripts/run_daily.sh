#!/usr/bin/env bash
# ==============================================
#   SMM 锂电采集 - 每日采集入口 (Linux, Supervisor)
#   由 crontab 调用，也可手动运行
# ==============================================
# 用法:
#   bash scripts/run_daily.sh              # 采集今天
#   bash scripts/run_daily.sh --dry-run     # 试运行
#   bash scripts/run_daily.sh --date 2026-08-12  # 指定日期
#   bash scripts/run_daily.sh --headed      # 有界面（调试用）
#   bash scripts/run_daily.sh --category 锂金属
# ==============================================
#
# 链路（2026-09-18 V2 切换后）：
#   cron 9:05  ─┐
#               ├─→ run_daily.sh ─→ smm_supervisor.py ─→ legacy_main.collect
#   cron 9:30  ─┘                    ├ network preflight
#                                    ├ persistent profile auth check
#                                    ├ auto-login (auto_login_enabled=true 时)
#                                    ├ collector
#                                    ├ validator
#                                    ├ DB upsert
#                                    ├ post-run verify
#                                    └ write auth_status + collector_status
#
# run-level lock：/var/lock/smm-collector-run.lock (0600 owner=root)
#   - 防止 09:05/09:30/@reboot catchup/手动 触发并发采集
#   - 已有任务在跑时新启动安全退出（exit 0）
# ==============================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

# 检查虚拟环境
if [ ! -f .venv/bin/python ]; then
  echo "[ERROR] 虚拟环境不存在: ${ROOT}/.venv"
  echo "请先运行: bash scripts/deploy_server.sh"
  exit 1
fi

# 确保日志目录存在
mkdir -p logs

# ── Run-level flock（防止并发采集） ─────────────────────
#   锁位置：/var/lock/smm-collector-run.lock（system-wide，owner=root）
#   - 必须 root 创建，0600
#   - flock -n：非阻塞；持有时立即退出
RUN_LOCK="/var/lock/smm-collector-run.lock"
if [ ! -e "$RUN_LOCK" ]; then
  # 兜底：脚本首次运行时确保锁文件存在（0600 owner=root）
  touch "$RUN_LOCK" 2>/dev/null || true
  chmod 600 "$RUN_LOCK" 2>/dev/null || true
  chown root:root "$RUN_LOCK" 2>/dev/null || true
fi

exec 9>"$RUN_LOCK"
if ! flock -n 9; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') [skip] 已有采集任务在运行（run_lock=${RUN_LOCK}），本次退出"
  exit 0
fi

# ── 执行采集（透传所有参数） ───────────────────────────
echo "$(date '+%Y-%m-%d %H:%M:%S') [start] supervisor run_daily args=$*"
.venv/bin/python scripts/smm_supervisor.py "$@"
EXIT_CODE=$?

# 记录退出码
echo "$(date '+%Y-%m-%d %H:%M:%S') [exit] supervisor exit_code=${EXIT_CODE}" >> logs/task_scheduler_exit.log

exit $EXIT_CODE
