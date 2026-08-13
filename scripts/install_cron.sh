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
# SMM 锂电现货发布时间不固定（实测 10:17~12:04 均出现过），故：
#   10:00 早采（发布早时可当日入库）
#   11:00 铜铝镍延迟补采（retry_metals.py）
#   12:30 全量兜底重跑（当日数据未发布时正式固定汇总不更新，靠这次兜底）
CRON_DAILY="0 10 * * 1-5 /bin/bash ${ROOT}/scripts/run_daily.sh >> ${ROOT}/logs/cron.log 2>&1"
CRON_METALS="0 11 * * 1-5 ${ROOT}/.venv/bin/python ${ROOT}/scripts/retry_metals.py >> ${ROOT}/logs/cron_metals.log 2>&1"
CRON_NOON="30 12 * * 1-5 /bin/bash ${ROOT}/scripts/run_daily.sh >> ${ROOT}/logs/cron.log 2>&1"
MARKER="# SMM 锂电采集定时任务（由 install_cron.sh 管理）"

# ── 生成新的 crontab ──────────────────────────────────────────
gen_crontab() {
    crontab -l 2>/dev/null || true
    echo ""
    echo "$MARKER"
    echo "$CRON_DAILY"
    echo "$CRON_METALS"
    echo "$CRON_NOON"
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
