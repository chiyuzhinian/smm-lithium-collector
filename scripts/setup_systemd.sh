#!/usr/bin/env bash
# ==============================================
#   SMM 锂电采集 - 安装 systemd 服务
# ==============================================
# 用法:
#   bash scripts/setup_systemd.sh            # 安装全部服务
#   bash scripts/setup_systemd.sh --status   # 查看服务状态
#   bash scripts/setup_systemd.sh --remove   # 移除全部服务
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

# ── 服务定义 ──────────────────────────────────────────────────

FILESERVER_SERVICE="smm-fileserver"
FILESERVER_UNIT="[Unit]
Description=锂电价格与回收业务数据中心门户 (端口8888)
After=network.target

[Service]
Type=simple
User=smmweb
Group=smmweb
WorkingDirectory=${ROOT}
ExecStart=${ROOT}/.venv/bin/python ${ROOT}/scripts/file_server.py
Restart=always
RestartSec=10
# 账号库可写目录（ProtectSystem=strict 下唯一可写路径；自动创建并归属 smmweb）
StateDirectory=smm-fileserver
StateDirectoryMode=0700
# 安全加固：禁止提权、私有 /tmp、只读文件系统视图（不遮 /root，服务需要读数据目录）
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target"

NGROK_SERVICE="smm-ngrok"
NGROK_UNIT="[Unit]
Description=SMM Ngrok Tunnel (8888端口内网穿透)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${ROOT}
ExecStart=${ROOT}/ngrok http 8888
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target"

# ── 函数 ──────────────────────────────────────────────────────

install_service() {
    local name="$1"
    local unit="$2"
    local unit_path="/etc/systemd/system/${name}.service"

    if [ -f "$unit_path" ]; then
        echo -e "${YELLOW}[SKIP]${NC} ${name} 已存在: ${unit_path}"
        return 0
    fi

    echo "$unit" | sudo tee "$unit_path" > /dev/null
    sudo systemctl daemon-reload
    sudo systemctl enable "$name" --now
    echo -e "${GREEN}[OK]${NC} ${name} 已安装并启动"
}

remove_service() {
    local name="$1"
    local unit_path="/etc/systemd/system/${name}.service"

    if [ -f "$unit_path" ]; then
        sudo systemctl stop "$name" 2>/dev/null || true
        sudo systemctl disable "$name" 2>/dev/null || true
        sudo rm -f "$unit_path"
        sudo systemctl daemon-reload
        echo -e "${GREEN}[OK]${NC} ${name} 已移除"
    else
        echo -e "${YELLOW}[SKIP]${NC} ${name} 不存在"
    fi
}

show_status() {
    echo "系统服务状态："
    echo "────────────────────────────────────────────"
    for svc in "$FILESERVER_SERVICE" "$NGROK_SERVICE"; do
        if systemctl is-enabled "$svc" &>/dev/null; then
            printf "%-30s %s\n" "$svc" "$(systemctl is-active "$svc")"
        else
            printf "%-30s %s\n" "$svc" "未安装"
        fi
    done
    echo "────────────────────────────────────────────"
}

# ── 主逻辑 ────────────────────────────────────────────────────

# 必须 root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ERROR]${NC} 此脚本需要 root 权限（需要写 /etc/systemd/system/）"
    echo "请使用: sudo bash scripts/setup_systemd.sh"
    exit 1
fi

case "$MODE" in
    --status|-s)
        show_status
        exit 0
        ;;
    --remove|-r)
        echo "移除 systemd 服务..."
        remove_service "$FILESERVER_SERVICE"
        remove_service "$NGROK_SERVICE"
        sudo systemctl daemon-reload
        echo -e "${GREEN}[OK]${NC} 清理完成"
        exit 0
        ;;
    install)
        echo "安装 systemd 服务..."
        install_service "$FILESERVER_SERVICE" "$FILESERVER_UNIT"
        echo -e "${YELLOW}[提示]${NC} 请确保已运行 bash scripts/secure_fileserver.sh 完成权限加固"

        # ngrok：仅当 ngrok 二进制存在时安装
        if [ -f "${ROOT}/ngrok" ]; then
            install_service "$NGROK_SERVICE" "$NGROK_UNIT"
        else
            echo -e "${YELLOW}[INFO]${NC} ngrok 未安装（${ROOT}/ngrok 不存在），跳过 smm-ngrok 服务"
            echo "  如果需要 ngrok 内网穿透，请下载 Linux 版 ngrok 并放到项目根目录"
            echo "  下载地址: https://ngrok.com/download"
        fi

        echo ""
        show_status
        echo ""
        echo -e "${GREEN}✅ systemd 服务安装完成${NC}"
        ;;
    *)
        echo "用法: sudo bash scripts/setup_systemd.sh [--status|--remove]"
        exit 1
        ;;
esac
