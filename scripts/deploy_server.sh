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
    echo "    MYSQL_AUTO_SYNC_AFTER_COLLECTION=false"
    echo ""
    warn "请编辑 .env 后重新运行本脚本，或稍后手动配置"
else
    ok ".env 已存在"
fi

# data/auth 目录
mkdir -p data/auth data/database data/raw data/exports data/screenshots logs

# V2 认证说明：主认证 = 持久浏览器档案 /var/lib/smm-collector/browser-profile
#（备份包恢复），data/auth/storage_state.json 仅为 V1 fallback。
if [ -d /var/lib/smm-collector/browser-profile ]; then
    ok "持久浏览器档案已就位: /var/lib/smm-collector/browser-profile"
elif [ -f data/auth/storage_state.json ]; then
    warn "未发现 V2 持久档案；存在 V1 fallback 登录态（data/auth/storage_state.json）"
    warn "请按迁移指南恢复 /var/lib/smm-collector（含 browser-profile 与状态文件）"
else
    echo ""
    warn "==========================================="
    warn "  未找到任何 SMM 登录态！"
    warn "==========================================="
    echo ""
    echo "  请按 docs/SMM_AUTH_AND_COLLECTION_RUNBOOK.md 执行人工登录恢复："
    echo "    .venv/bin/python scripts/smm_auth_init.py --login   # 需 VNC/X 显示"
    echo "  或从备份包恢复 /var/lib/smm-collector/。"
    echo ""
fi

echo ""

# ── 6. 服务用户与后台服务（V2 生产架构，2026-10 迁移修订） ──────
echo "[6/6] 准备服务用户与后台服务..."

# 门户低权限运行用户（systemd 单元 User=smmweb，沙箱加固必需）
if ! id smmweb &>/dev/null; then
    sudo useradd --system --no-create-home --shell /usr/sbin/nologin smmweb
    ok "smmweb 用户已创建"
else
    info "smmweb 用户已存在，跳过"
fi

# 说明：本脚本不再直接写入 systemd 单元，也不再直接安装 crontab。
# - systemd：由 scripts/setup_systemd.sh 安装（V2 加固单元：smmweb 低权限 +
#   ProtectSystem=strict + StateDirectory=smm-fileserver，与生产一致）
# - 权限加固：bash scripts/secure_fileserver.sh（smmweb 最小只读 ACL）
# - cron：由 scripts/install_cron.sh 管理（9:05 主采集 / 9:30 兜底 / 10:00 哨兵 / @reboot），
#   支持 --dry-run 预览与 --remove 移除，且不会误删服务器上其它项目任务
# - 采集登录态（V2）：持久浏览器档案 /var/lib/smm-collector/browser-profile，
#   由备份包恢复，不再依赖 data/auth/storage_state.json 上传
info "systemd 服务安装: sudo bash scripts/setup_systemd.sh"
info "文件服务权限加固: sudo bash scripts/secure_fileserver.sh"
info "定时任务安装(先预览): bash scripts/install_cron.sh --dry-run && bash scripts/install_cron.sh"

ok "服务用户与部署提示完成"
echo ""

# ── 防火墙提示 ────────────────────────────────────────────────
echo "============================================"
echo "  ⚠️  防火墙/安全组配置（阿里云）"
echo "============================================"
echo ""
echo "  请在阿里云控制台 → 安全组 中开放端口："
echo ""
echo "  ┌────────┬────────┬──────────────────────────────┐"
echo "  │ 端口   │ 协议   │ 用途                         │"
echo "  ├────────┼────────┼──────────────────────────────┤"
echo "  │ 80     │ TCP    │ 门户入口（nginx 代理到 8888）│"
echo "  │ 22     │ TCP    │ SSH 远程管理                 │"
echo "  └────────┴────────┴──────────────────────────────┘"
echo ""
echo "  8888（门户内部端口）与 3306（MySQL）不对公网开放："
echo "  门户服务默认仅监听 127.0.0.1（FILE_SERVER_BIND），公网访问一律经 nginx 80。"
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
echo "  2. 恢复 V2 持久浏览器档案与系统状态（见 MIGRATION_GUIDE.md §3.3）："
echo "     /var/lib/smm-collector（browser-profile + 状态文件，0700 root）"
echo "     /var/lib/smm-fileserver（auth.db 三件套，0700 smmweb，停服状态下恢复）"
echo "     /etc/smm-collector（secrets.env，0700/0600 root）"
echo ""
echo "  3. 安装 systemd 与权限加固："
echo "     sudo bash ${ROOT}/scripts/setup_systemd.sh"
echo "     sudo bash ${ROOT}/scripts/secure_fileserver.sh"
echo ""
echo "  4. 安装定时任务（先预览再安装）："
echo "     bash ${ROOT}/scripts/install_cron.sh --dry-run"
echo "     bash ${ROOT}/scripts/install_cron.sh"
echo ""
echo "  5. 测试运行："
echo "     bash ${ROOT}/scripts/run_daily.sh --dry-run"
echo ""
echo "  6. 验证文件服务器（经 nginx 80）："
echo "     curl http://127.0.0.1:8888/health"
echo ""
echo "定时任务（由 install_cron.sh 管理）："
echo "  周一至周五 09:05 主采集 / 09:30 兜底补采 / 10:00 健康哨兵 / @reboot 开机补采"
echo ""
echo "运维命令："
echo "  查看日志:      tail -f ${ROOT}/logs/collector_\$(date +%Y-%m-%d).log"
echo "  查看定时日志:  tail -f ${ROOT}/logs/cron.log"
echo "  重启文件服务:  sudo systemctl restart smm-fileserver"
echo "  查看服务状态:  sudo systemctl status smm-fileserver"
echo "  查看数据库:    sqlite3 ${ROOT}/data/database/smm_lithium.db"
echo ""
