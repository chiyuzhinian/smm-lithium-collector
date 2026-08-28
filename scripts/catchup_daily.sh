#!/usr/bin/env bash
# ==============================================
#   SMM 锂电采集 - 错过 9:05 定时任务的兜底补采
# ==============================================
# 触发点（由 install_cron.sh 写入 crontab）：
#   @reboot（开机后延迟 60s，等网络就绪）
#   工作日 9:30 重试（覆盖 9:05 任务执行失败但未宕机的情况）
#
# 行为：仅当今天(target_date)尚无采集记录，或最近一次采集
#   status != success 时才执行采集；否则跳过。
# 防重复：DB 状态判断 + run_daily.sh 内的 flock 互斥锁。
# 页面日期滚动风险：SMM 部分品种 9:30~10:30 翻日，越早补采越完整。
#
# 用法:
#   bash scripts/catchup_daily.sh            # 正常兜底
#   bash scripts/catchup_daily.sh --check    # 仅打印判断结果，不执行
# ==============================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

MODE="${1:-run}"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') catchup: $*"; }

# 周末跳过（采集器只在工作日运行，周五数据周一早上正常采集）
if [ "$(date +%u)" -gt 5 ]; then
    log "周末，跳过"
    exit 0
fi

# 查询 collection_runs：今天(target_date)的最新一次采集状态
TODAY="$(date +%F)"
STATE="$(TODAY="$TODAY" .venv/bin/python -c '
import os, sqlite3
today = os.environ["TODAY"]
try:
    c = sqlite3.connect("data/database/smm_lithium.db")
    row = c.execute(
        "SELECT status FROM collection_runs WHERE target_date=? ORDER BY started_at DESC LIMIT 1",
        (today,),
    ).fetchone()
except Exception as e:
    print("error:%s" % e)
    raise SystemExit(0)
print("none" if row is None else row[0])
')"

case "$STATE" in
    success)
        log "今日已采集成功，跳过"
        exit 0
        ;;
    none|failed|partial_success)
        log "今日采集缺失或未全成功(状态=$STATE)，需要补采"
        if [ "$MODE" = "--check" ]; then
            log "[check] 将执行 run_daily.sh"
            exit 0
        fi
        exec /bin/bash "$ROOT/scripts/run_daily.sh"
        ;;
    *)
        log "无法判断($STATE)，跳过"
        exit 0
        ;;
esac
