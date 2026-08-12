#!/usr/bin/env bash
# ==============================================
#   SMM 锂电采集 - 每日采集入口 (Linux)
#   由 crontab 调用，也可手动运行
# ==============================================
# 用法:
#   bash scripts/run_daily.sh              # 采集今天
#   bash scripts/run_daily.sh --dry-run     # 试运行
#   bash scripts/run_daily.sh --date 2026-08-12  # 指定日期
#   bash scripts/run_daily.sh --headed      # 有界面（调试用）
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

# 执行采集（透传所有参数）
echo "$(date '+%Y-%m-%d %H:%M:%S') 开始采集..."
.venv/bin/python scripts/run_daily.py "$@"
EXIT_CODE=$?

# 记录退出码
echo "$(date '+%Y-%m-%d %H:%M:%S') scheduled_daily exit_code=${EXIT_CODE}" >> logs/task_scheduler_exit.log

exit $EXIT_CODE
