# 服务器数据同步与 MySQL 配置操作手册

> 给服务器端 Claude 执行的 Step-by-Step 操作指南。
> **使用前请先填写下方「凭据清单」中的占位符。**

---

## ⚠️ 凭据清单（执行前必须填写）

文档中所有 `<...>` 占位符需要替换为实际值。用编辑器全局搜索替换：

| 占位符 | 说明 | 从哪里获取 |
| ------ | ---- | ---------- |
| `<服务器公网IP>` | 百度云服务器的公网 IP | `curl -s ifconfig.me` 或百度云控制台 |
| `<SMM账号>` | SMM 登录手机号 | 本机 `.env` 中的 `SMM_USERNAME` |
| `<SMM密码>` | SMM 登录密码 | 本机 `.env` 中的 `SMM_PASSWORD` |
| `<MySQL密码>` | MySQL root 密码 | 建议和本机 `.env` 中 `MYSQL_PASSWORD` 一致 |
| `<钉钉Webhook>` | 钉钉机器人 Webhook 完整 URL | 本机 `.env` 中的 `DINGTALK_WEBHOOK` |
| `<钉钉Secret>` | 钉钉机器人签名密钥 | 本机 `.env` 中的 `DINGTALK_SECRET` |

> 本机 `.env` 路径：`C:\科研\smm_lithium_collector\.env`

---

## 前提条件

- 服务器已执行 `bash scripts/deploy_server.sh` 完成基础部署
- 项目路径：`/root/smm-lithium-collector`
- 本机（Windows）已通过 SCP 上传以下文件到服务器：

```
/root/smm-lithium-collector/data/database/smm_lithium.db    ← SQLite 数据库
/root/smm-lithium-collector/data/auth/storage_state.json    ← SMM 登录态
/root/smm-lithium-collector/data/exports/                    ← 历史导出文件 (可选)
```

如果尚未上传，先在本机 Windows 执行（替换 `<服务器公网IP>`）：

```bash
scp "C:\科研\smm_lithium_collector\data\database\smm_lithium.db" root@<服务器公网IP>:/root/smm-lithium-collector/data/database/
scp "C:\科研\smm_lithium_collector\data\auth\storage_state.json" root@<服务器公网IP>:/root/smm-lithium-collector/data/auth/
scp -r "C:\科研\smm_lithium_collector\data\exports\*" root@<服务器公网IP>:/root/smm-lithium-collector/data/exports/
```

---

## 步骤 1：验证上传文件

```bash
cd /root/smm-lithium-collector

# 检查 SQLite 数据库
.venv/bin/python -c "
import sqlite3
db = sqlite3.connect('data/database/smm_lithium.db')
print('总记录数:', db.execute('SELECT COUNT(*) FROM lithium_spot_prices').fetchone()[0])
print('最近 5 天:')
for row in db.execute('SELECT price_date, COUNT(*) FROM lithium_spot_prices GROUP BY price_date ORDER BY price_date DESC LIMIT 5'):
    print(f'  {row[0]}: {row[1]} 条')
"

# 检查登录态
ls -lh data/auth/storage_state.json
```

---

## 步骤 2：配置 .env 文件

```bash
cat > .env << 'ENVEOF'
# SMM 采集
SMM_USERNAME=<SMM账号>
SMM_PASSWORD=<SMM密码>
SMM_LOGIN_URL=https://user.smm.cn/login
SMM_TARGET_URL=https://new-energy.smm.cn/new_energy/14042
SMM_HEADLESS=true
SMM_TIMEOUT=30000

# MySQL（本地服务器）
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=<MySQL密码>
MYSQL_DATABASE=smm_lithium
MYSQL_CHARSET=utf8mb4
MYSQL_CONNECT_TIMEOUT=10
MYSQL_SYNC_BATCH_SIZE=500
MYSQL_SYNC_MAX_RETRIES=3
MYSQL_SYNC_RETRY_INTERVAL=5
MYSQL_AUTO_CREATE_DATABASE=true
MYSQL_AUTO_SYNC_AFTER_COLLECTION=true

# 钉钉通知
DINGTALK_WEBHOOK=<钉钉Webhook>
DINGTALK_SECRET=<钉钉Secret>

# 文件下载（填服务器公网IP）
FILE_HOST=http://<服务器公网IP>:8888
ENVEOF
```

> 执行前请确保所有 `<...>` 已替换为实际值。公网 IP 可通过 `curl -s ifconfig.me` 获取。

---

## 步骤 3：安装 MySQL Server

```bash
# 安装
sudo apt install -y mysql-server

# 启动
sudo systemctl enable mysql --now

# 设置 root 密码
sudo mysql -u root << 'SQL'
ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '<MySQL密码>';
FLUSH PRIVILEGES;
SQL
```

---

## 步骤 4：创建 SMM 数据库

```bash
mysql -u root -p'<MySQL密码>' << 'SQL'
CREATE DATABASE IF NOT EXISTS smm_lithium
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

SHOW DATABASES;
SQL
```

---

## 步骤 5：同步 SQLite 历史数据到 MySQL

```bash
cd /root/smm-lithium-collector

# 全量同步
.venv/bin/python scripts/sync_to_mysql.py --full
```

预期输出类似：

```
Source : .../smm_lithium.db
Target : MySQL 127.0.0.1:3306/smm_lithium
Mode   : full

==================================================
MySQL 同步完成
批次     : sync_20260812_...
SQLite读取: 1634
新增      : 1634
状态      : success
==================================================
```

---

## 步骤 6：验证

```bash
# 验证 MySQL 数据
mysql -u root -p'<MySQL密码>' smm_lithium -e "
  SELECT price_date, COUNT(*) as cnt
  FROM smm_price_records
  GROUP BY price_date
  ORDER BY price_date DESC
  LIMIT 10;
"

# 验证采集能正常运行
bash scripts/run_daily.sh --dry-run
```

---

## 步骤 7：重启文件服务器

```bash
sudo systemctl restart smm-fileserver
sudo systemctl status smm-fileserver
```

---

## 步骤 8：钉钉通知测试

```bash
# 发送测试消息
.venv/bin/python -c "
import asyncio
from smm_collector.notifier import send_dingtalk
asyncio.run(send_dingtalk('🚀 SMM采集服务器部署成功', '服务已就绪，明天起每日9:00自动采集'))
" && echo "钉钉消息发送成功" || echo "钉钉消息发送失败，请检查 .env 中的 DINGTALK_WEBHOOK 和 DINGTALK_SECRET"
```

---

## 完成检查清单

- [ ] SQLite 数据库已上传，记录数 ≥ 1600
- [ ] `.env` 已配置，所有 `<...>` 已替换为实际值
- [ ] MySQL 已安装并启动
- [ ] `smm_lithium` 数据库已创建
- [ ] `--full` 同步成功，MySQL 中有历史数据
- [ ] `--dry-run` 试运行成功
- [ ] 文件服务器正常运行（端口 8888）
- [ ] `curl http://<服务器公网IP>:8888/` 可外网访问
- [ ] 钉钉群收到测试消息
- [ ] `crontab -l` 确认定时任务已安装

---

## 后续日常运维

```bash
# 每日采集（自动运行，也可手动触发）
bash scripts/run_daily.sh

# 查看当天日志
tail -f logs/collector_$(date +%Y-%m-%d).log

# 查看 MySQL 同步状态
mysql -u root -p'<MySQL密码>' smm_lithium -e "
  SELECT sync_status, COUNT(*) FROM smm_sync_runs
  GROUP BY sync_status;
"

# 登录过期时，在本机重新登录后上传：
# scp "C:\科研\smm_lithium_collector\data\auth\storage_state.json" root@<服务器公网IP>:/root/smm-lithium-collector/data/auth/

# Cron 定时任务
# 周一至周五 9:00  全量采集 + MySQL 同步 + 钉钉通知
# 周一至周五 11:00 铜铝镍延迟补采
```
