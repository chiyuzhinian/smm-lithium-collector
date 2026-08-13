#!/usr/bin/env bash
# ==============================================
#   SMM 文件服务器最小权限加固
# ==============================================
# 为 smmweb 用户（systemd 运行用户）授予最小只读权限：
#   - 可遍历 /root 与项目目录（x）
#   - 可读 static / data/exports / config（rX）
#   - 可读 SQLite 数据库（r，只读连接由代码 mode=ro 保证）
# 采集脚本仍以 root 运行，不受影响。可重复执行。
# ==============================================
set -euo pipefail

ROOT="/root/smm-lithium-collector"
USER="smmweb"

if ! command -v setfacl >/dev/null 2>&1; then
    echo "[ERROR] 缺少 setfacl，请先: apt-get install -y acl"
    exit 1
fi
if ! id "$USER" >/dev/null 2>&1; then
    echo "[ERROR] 用户 $USER 不存在，请先: useradd --system --no-create-home --shell /usr/sbin/nologin $USER"
    exit 1
fi

echo "[1/4] 授予 /root 与项目目录遍历权限"
setfacl -m u:"$USER":x /root
setfacl -m u:"$USER":x "$ROOT"
setfacl -m u:"$USER":x "$ROOT/data"

echo "[2/4] 授予静态页面与导出目录只读权限"
setfacl -R -m u:"$USER":rX "$ROOT/static"
setfacl -R -m u:"$USER":rX "$ROOT/data/exports"
setfacl -R -m u:"$USER":rX "$ROOT/config"

echo "[3/4] 授予 SQLite 数据库只读权限"
setfacl -m u:"$USER":r "$ROOT/data/database/smm_lithium.db"
setfacl -m u:"$USER":x "$ROOT/data/database"

echo "[4/4] 验证（以 $USER 身份试读关键路径）"
setpriv --reuid="$USER" --regid="$USER" --clear-groups \
    "$ROOT/.venv/bin/python" -c "
import sqlite3
conn = sqlite3.connect('file:$ROOT/data/database/smm_lithium.db?mode=ro', uri=True)
n = conn.execute('select count(*) from lithium_spot_prices').fetchone()[0]
conn.close()
print(f'  DB 可读: {n} 行')
print('  exports 可读:', __import__('os').path.isdir('$ROOT/data/exports'))
print('  static 可读:', __import__('os').path.isdir('$ROOT/static'))
"
echo "[OK] 权限加固完成"
