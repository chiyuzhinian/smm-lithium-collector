#!/usr/bin/env bash
# ==============================================
#   SMM 锂电采集 - 安装 Cron 定时任务
# ==============================================
# 用法:
#   bash scripts/install_cron.sh            # 安装全部定时任务
#   bash scripts/install_cron.sh --dry-run  # 仅打印，不写入
#   bash scripts/install_cron.sh --remove   # 移除 SMM 相关任务
# ==============================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

MODE="${1:-install}"

# ── 定时任务内容 ──────────────────────────────────────────────
# 次日采集模式：每天 9:05 采集前一天（页面有数据的最近交易日）数据，
# 当天不采集当日数据（周五数据周一早上入库）。采集内含页面数据日期校准、
# 验证、固定汇总门控、MySQL 同步，无需当天的兜底/补采任务。
CRON_DAILY="5 9 * * 1-5 /bin/bash ${ROOT}/scripts/run_daily.sh >> ${ROOT}/logs/cron.log 2>&1"
MARKER="# SMM 锂电采集定时任务（由 install_cron.sh 管理）"

# ── 生成新的 crontab ──────────────────────────────────────────
gen_crontab() {
    crontab -l 2>/dev/null || true
    echo ""
    echo "$MARKER"
    echo "$CRON_DAILY"
}

# ── 移除 SMM 任务 ────────────────────────────────────────────
remove_smm_tasks() {
    local tmp
    tmp=$(mktemp)
    crontab -l 2>/dev/null | grep -v "$MARKER" | grep -v "run_daily.sh" | grep -v "retry_metals.py" > "$tmp" || true
    if [ -s "$tmp" ]; then
        # 删除末尾多余空行
        sed -i -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$tmp" 2>/dev/null || true
        crontab "$tmp"
        echo -e "${GREEN}[OK]${NC} SMM 定时任务已移除"
    else
        # 空文件 = 清空 crontab
        crontab "$tmp" 2>/dev/null || true
        echo -e "${GREEN}[OK]${NC} SMM 定时任务已移除（crontab 已清空）"
    fi
    rm -f "$tmp"
}

# ── 主逻辑 ────────────────────────────────────────────────────
case "$MODE" in
    --dry-run|-n)
        echo -e "${YELLOW}[DRY-RUN]${NC} 以下内容将写入 crontab："
        echo "────────────────────────────────────────────"
        gen_crontab
        echo "────────────────────────────────────────────"
        ;;
    --remove|-r)
        remove_smm_tasks
        ;;
    *)
        # 先移除旧任务，再写入新任务（防重复）
        remove_smm_tasks
        tmp=$(mktemp)
        gen_crontab > "$tmp"
        crontab "$tmp"
        rm -f "$tmp"
        echo -e "${GREEN}[OK]${NC} SMM 定时任务已安装"
        echo ""
        echo "当前 crontab:"
        echo "────────────────────────────────────────────"
        crontab -l | grep -v '^$'
        echo "────────────────────────────────────────────"
        ;;
esac
