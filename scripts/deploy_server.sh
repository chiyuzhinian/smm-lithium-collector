#!/usr/bin/env bash
# ==============================================
#   SMM 锂电采集 - Ubuntu 服务器一键部署
# ==============================================
set -euo pipefail

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# 定位项目根目录（脚本在 scripts/ 下）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

echo "============================================"
echo "  SMM 锂电采集 - 服务器一键部署"
echo "============================================"
echo "部署目录: ${ROOT}"
echo ""

# ── 1. 系统环境检查 ──────────────────────────────────────────
echo "[1/6] 检查系统环境..."

# 检测 Ubuntu 版本
if [ -f /etc/os-release ]; then
    . /etc/os-release
    info "操作系统: ${NAME} ${VERSION_ID}"
else
    warn "无法检测操作系统，继续..."
fi

# 检测 Python
PYTHON=""
for py in python3.11 python3.12 python3; do
    if command -v "$py" &>/dev/null; then
        PYVER=$("$py" --version 2>&1 | grep -oP '\d+\.\d+')
        MAJOR=$(echo "$PYVER" | cut -d. -f1)
        MINOR=$(echo "$PYVER" | cut -d. -f2)
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 11 ]; then
            PYTHON="$py"
            ok "Python ${PYVER} ($py)"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    error "未找到 Python 3.11+，正在安装..."
    sudo apt update
    sudo apt install -y python3.11 python3.11-venv python3-pip
    PYTHON="python3.11"
    ok "Python 3.11 安装完成"
fi

echo ""

# ── 2. 安装系统依赖 ──────────────────────────────────────────
echo "[2/6] 安装系统依赖..."

info "安装 Chromium 运行时库..."
sudo apt update
sudo apt install -y \
    libnss3 libnspr4 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
    libgbm1 libasound2 libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 \
    libgtk-3-0 libpango-1.0-0 libcairo2 libcups2 \
    libatspi2.0-0 libxshmfence1

info "安装中文字体（防止截图乱码）..."
sudo apt install -y fonts-noto-cjk fonts-noto-color-emoji

ok "系统依赖安装完成"
echo ""

# ── 3. Python 虚拟环境 ────────────────────────────────────────
echo "[3/6] 创建虚拟环境 + 安装 Python 依赖..."

if [ ! -d .venv ]; then
    "$PYTHON" -m venv .venv
    ok "虚拟环境创建完成"
else
    info "虚拟环境已存在，跳过创建"
fi

# 激活虚拟环境
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
ok "Python 依赖安装完成"
echo ""

# ── 4. Playwright Chromium ────────────────────────────────────
echo "[4/6] 安装 Playwright Chromium..."

playwright install chromium
playwright install-deps chromium 2>/dev/null || true
ok "Chromium 安装完成"
echo ""

# ── 5. 配置文件 ──────────────────────────────────────────────
echo "[5/6] 检查配置文件..."

# .env
if [ ! -f .env ]; then
    cp .env.example .env
    warn ".env 已从模板创建，请编辑填入实际配置"
    echo ""
    echo "  必填项："
    echo "    SMM_LOGIN_URL=https://user.smm.cn/login"
    echo "    SMM_TARGET_URL=https://new-energy.smm.cn/new_energy/14042"
    echo "    DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxx"
    echo "    DINGTALK_SECRET=SECxxx"
    echo "    FILE_HOST=http://你的公网IP:8888"
    echo "    MYSQL_AUTO_SYNC_AFTER_COLLECTION=false"
    echo ""
    warn "请编辑 .env 后重新运行本脚本，或稍后手动配置"
else
    ok ".env 已存在"
fi

# data/auth 目录
mkdir -p data/auth data/database data/raw data/exports data/screenshots logs

if [ -f data/auth/storage_state.json ]; then
    ok "登录态文件已存在: data/auth/storage_state.json"
else
    echo ""
    warn "==========================================="
    warn "  未找到登录态文件！"
    warn "==========================================="
    echo ""
    echo "  请在你的 Windows 本机执行："
    echo ""
    echo "    # 1. 本机登录 SMM"
    echo "    .venv\\Scripts\\python.exe scripts\\manual_login.py"
    echo ""
    echo "    # 2. 上传登录态到服务器"
    echo "    scp data\\auth\\storage_state.json root@你的服务器IP:${ROOT/#$HOME/~}/data/auth/"
    echo ""
    echo "  Cookie 有效期通常 7-30 天，过期后需重新上传。"
    echo ""
fi

echo ""

# ── 6. 安装 systemd 服务 + crontab ─────────────────────────────
echo "[6/6] 安装后台服务..."

# systemd 文件服务器
SERVICE_FILE="/etc/systemd/system/smm-fileserver.service"
if [ ! -f "$SERVICE_FILE" ]; then
    sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=SMM File Server (端口8888)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${ROOT}
ExecStart=${ROOT}/.venv/bin/python ${ROOT}/scripts/file_server.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable smm-fileserver --now
    ok "systemd 文件服务器已安装并启动"
else
    info "systemd 服务已存在，跳过"
    sudo systemctl restart smm-fileserver 2>/dev/null || true
fi

# crontab 定时任务
CRON_TMP=$(mktemp)
crontab -l 2>/dev/null > "$CRON_TMP" || true

# 只在不存在时添加（防重复）
if ! grep -q "run_daily.sh" "$CRON_TMP" 2>/dev/null; then
    cat >> "$CRON_TMP" << 'CRONEOF'

# SMM 锂电采集 — 工作日 9:00 全量采集
0 9 * * 1-5 /bin/bash /root/smm-lithium-collector/scripts/run_daily.sh >> /root/smm-lithium-collector/logs/cron.log 2>&1

# SMM 金属补采 — 工作日 11:00 铜铝镍延迟重试
0 11 * * 1-5 /root/smm-lithium-collector/.venv/bin/python /root/smm-lithium-collector/scripts/retry_metals.py >> /root/smm-lithium-collector/logs/cron_metals.log 2>&1
CRONEOF
    crontab "$CRON_TMP"
    ok "crontab 定时任务已安装"
else
    info "crontab 已存在，跳过"
fi
rm -f "$CRON_TMP"

ok "后台服务安装完成"
echo ""

# ── 防火墙提示 ────────────────────────────────────────────────
echo "============================================"
echo "  ⚠️  防火墙/安全组配置"
echo "============================================"
echo ""
echo "  请在百度云控制台 → 安全组 中开放端口："
echo ""
echo "  ┌────────┬────────┬──────────────────────┐"
echo "  │ 端口   │ 协议   │ 用途                 │"
echo "  ├────────┼────────┼──────────────────────┤"
echo "  │ 8888   │ TCP    │ 文件下载（钉钉链接） │"
echo "  │ 22     │ TCP    │ SSH 远程管理         │"
echo "  └────────┴────────┴──────────────────────┘"
echo ""
echo "  服务器内防火墙："
echo "    sudo ufw allow 8888/tcp"
echo ""

# ── 部署摘要 ──────────────────────────────────────────────────
echo "============================================"
echo "  ✅ 部署完成！"
echo "============================================"
echo ""
echo "公网 IP: $(curl -s ifconfig.me 2>/dev/null || echo '请手动查询')"
echo ""
echo "下一步操作："
echo ""
echo "  1. 编辑 .env（如未配置）："
echo "     vim ${ROOT}/.env"
echo ""
echo "  2. 上传登录态（如未上传）："
echo "     scp data\\auth\\storage_state.json root@你的IP:${ROOT/#$HOME/~}/data/auth/"
echo ""
echo "  3. 测试运行："
echo "     bash ${ROOT}/scripts/run_daily.sh --dry-run"
echo ""
echo "  4. 正式采集："
echo "     bash ${ROOT}/scripts/run_daily.sh"
echo ""
echo "  5. 验证文件服务器："
echo "     curl http://你的IP:8888/"
echo ""
echo "定时任务（已安装）："
echo "  周一至周五 9:00  - 全量采集 + 报表 + 钉钉"
echo "  周一至周五 11:00 - 铜铝镍延迟补采"
echo ""
echo "运维命令："
echo "  查看日志:      tail -f ${ROOT}/logs/collector_\$(date +%Y-%m-%d).log"
echo "  查看定时日志:  tail -f ${ROOT}/logs/cron.log"
echo "  重启文件服务:  sudo systemctl restart smm-fileserver"
echo "  查看服务状态:  sudo systemctl status smm-fileserver"
echo "  查看数据库:    sqlite3 ${ROOT}/data/database/smm_lithium.db"
echo ""
