# ☁️ 公网服务器部署指南

把 SMM 锂电采集项目部署到云服务器，每天早上 9:00 自动采集，通过钉钉群发送日报和下载链接。

---

## 一、服务器选购

最低配置：**2核2G 50G 硬盘**

| 云厂商 | 产品 | 月费 |
|--------|------|------|
| 百度云 | 云服务器 BCC | ~70元起 |
| 阿里云 | 轻量应用服务器 | ~68元 |
| 腾讯云 | 轻量应用服务器 | ~65元 |

操作系统选 **Ubuntu 22.04 LTS**（或 24.04）。

---

## 二、百度云特定步骤

### 2.1 创建服务器

1. 登录 [百度云控制台](https://console.bce.baidu.com/)
2. 产品 → 云服务器 BCC → 创建实例
3. 镜像选择：**Ubuntu 22.04 LTS**（公共镜像）
4. 计费：包年包月 / 按量付费
5. 分配公网 IP
6. SSH 密钥或密码登录

### 2.2 安全组配置

在百度云控制台 → 安全组 → 添加入站规则：

| 端口 | 协议 | 来源 | 用途 |
| ---- | ---- | ---- | ---- |
| 8888 | TCP | 0.0.0.0/0 | 文件下载（钉钉链接用） |
| 22 | TCP | 0.0.0.0/0 | SSH 远程管理 |

### 2.3 SSH 登录

```bash
ssh root@你的服务器公网IP
```

---

## 三、一键部署（推荐）

项目提供了 Linux 一键部署脚本，SSH 登录后执行：

```bash
# 1. 克隆项目
git clone https://github.com/chiyuzhinian/smm-lithium-collector.git
cd smm-lithium-collector

# 2. 运行一键部署
sudo bash scripts/deploy_server.sh
```

部署脚本会自动完成：
- ✅ 安装 Python 3.11+ 和 Chromium 系统依赖
- ✅ 安装中文字体（防止 Playwright 截图乱码）
- ✅ 创建虚拟环境 + pip install
- ✅ 安装 Playwright Chromium 浏览器
- ✅ 配置 systemd 文件服务器（端口 8888，开机自启）
- ✅ 安装 crontab 定时任务（工作日 9:00 + 11:00）
- ✅ 输出部署摘要和下一步指引

### 部署后必须手动完成的步骤：

```bash
# 1. 编辑 .env（填入 SMM 目标 URL + 钉钉 Webhook + 公网 IP）
vim .env

# 2. 从本机上传登录态（关键！）
#    在 Windows 本机先运行一次 scripts/manual_login.py 登录 SMM
#    然后 scp 上传：
#    scp data\auth\storage_state.json root@你的IP:~/smm-lithium-collector/data/auth/

# 3. 测试运行
bash scripts/run_daily.sh --dry-run

# 4. 正式采集
bash scripts/run_daily.sh
```

---

## 四、手动部署（分步操作）

如果不使用一键脚本，按以下步骤操作：

### 4.1 环境安装

```bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装 Python 3.11
sudo apt install -y python3.11 python3.11-venv python3-pip

# 安装 Chromium 运行时依赖
sudo apt install -y libnss3 libnspr4 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
  libgbm1 libasound2 libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 \
  libgtk-3-0 libpango-1.0-0 libcairo2 libcups2 libatspi2.0-0 libxshmfence1

# 安装中文字体（防止截图乱码）
sudo apt install -y fonts-noto-cjk fonts-noto-color-emoji

# 克隆项目
git clone https://github.com/chiyuzhinian/smm-lithium-collector.git
cd smm-lithium-collector

# 创建虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 安装浏览器
playwright install chromium
playwright install-deps chromium
```

### 4.2 配置文件

```bash
cp .env.example .env
vim .env
```

```ini
# SMM 采集配置
SMM_LOGIN_URL=https://user.smm.cn/login
SMM_TARGET_URL=https://new-energy.smm.cn/new_energy/14042
SMM_HEADLESS=true
SMM_TIMEOUT=30000

# MySQL（云服务器上不需要则关闭同步）
MYSQL_HOST=
MYSQL_USER=
MYSQL_PASSWORD=
MYSQL_DATABASE=
MYSQL_AUTO_SYNC_AFTER_COLLECTION=false

# 钉钉通知（从群机器人页面复制）
DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxx
DINGTALK_SECRET=SECxxx

# 文件下载（填服务器公网 IP）
FILE_HOST=http://你的公网IP:8888
```

### 4.3 上传登录态

```bash
# 在服务器上创建目录
mkdir -p data/auth

# 在本地 Windows 上执行：
scp C:\科研\smm_lithium_collector\data\auth\storage_state.json \
    root@你的服务器IP:~/smm-lithium-collector/data/auth/
```

> Cookie 有效期通常 7-30 天。过期后重新上传即可。

### 4.4 启动文件服务器

```bash
# 测试启动
.venv/bin/python scripts/file_server.py &

# 验证
curl http://localhost:8888/
```

#### 设为 systemd 服务（开机自启 + 崩溃重启）

```bash
sudo bash scripts/setup_systemd.sh
```

或手动创建：

```bash
sudo tee /etc/systemd/system/smm-fileserver.service << 'EOF'
[Unit]
Description=SMM File Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/smm-lithium-collector
ExecStart=/root/smm-lithium-collector/.venv/bin/python /root/smm-lithium-collector/scripts/file_server.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable smm-fileserver --now
sudo systemctl status smm-fileserver
```

### 4.5 安装定时任务

```bash
bash scripts/install_cron.sh
```

或手动添加：

```bash
crontab -e
```

```
0 9 * * 1-5 /bin/bash /root/smm-lithium-collector/scripts/run_daily.sh >> /root/smm-lithium-collector/logs/cron.log 2>&1
0 11 * * 1-5 /root/smm-lithium-collector/.venv/bin/python /root/smm-lithium-collector/scripts/retry_metals.py >> /root/smm-lithium-collector/logs/cron_metals.log 2>&1
```

`1-5` 表示周一至周五，周末自动跳过。

---

## 五、防火墙/安全组

在云服务器**安全组**（控制台网页操作）开放端口：

| 端口 | 协议 | 用途 |
| ---- | ---- | ---- |
| 8888 | TCP | 文件下载 |
| 22 | TCP | SSH 登录 |

服务器内防火墙：

```bash
sudo ufw allow 8888/tcp
sudo ufw allow 22/tcp
```

---

## 六、验证

```bash
# 1. 试运行（不写数据库）
bash scripts/run_daily.sh --dry-run

# 2. 正式运行
bash scripts/run_daily.sh

# 3. 检查输出文件
ls data/exports/$(date +%Y)/$(date +%m)/每日汇总/Excel/

# 4. 测试下载链接（用浏览器或 curl）
curl -I http://你的公网IP:8888/

# 5. 检查定时任务
crontab -l

# 6. 检查服务状态
sudo systemctl status smm-fileserver
```

---

## 七、历史数据迁移

如果本机已有历史采集数据，需要迁移到服务器。

### 7.1 迁移 SQLite 数据库（推荐）

最简单的方式：把本机 SQLite 数据库文件直接上传到服务器。

**在本机 Windows 上执行：**

```bash
scp C:\科研\smm_lithium_collector\data\database\smm_lithium.db \
    root@你的服务器IP:/root/smm-lithium-collector/data/database/
```

> ⚠️ 如果服务器上已有当天采集的新数据，scp 会直接覆盖。如果两边都有新数据，建议先备份服务器的 .db 文件，然后用下面的 MySQL 同步来合并。

**在服务器上验证：**

```bash
sqlite3 /root/smm-lithium-collector/data/database/smm_lithium.db \
  "SELECT price_date, COUNT(*) FROM lithium_spot_prices
   GROUP BY price_date ORDER BY price_date DESC LIMIT 10"
```

### 7.2 迁移导出文件（可选）

```bash
# 上传全部历史 Excel 导出文件
scp -r C:\科研\smm_lithium_collector\data\exports\ \
    root@你的服务器IP:/root/smm-lithium-collector/data/exports/
```

---

## 八、MySQL 安装与数据同步

### 8.1 安装 MySQL Server

```bash
sudo apt install -y mysql-server
sudo systemctl enable mysql --now
sudo mysql_secure_installation
```

### 8.2 创建数据库和用户

```bash
sudo mysql -u root
```

```sql
CREATE DATABASE IF NOT EXISTS smm_lithium
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'smm'@'localhost' IDENTIFIED BY '你的密码';

GRANT ALL PRIVILEGES ON smm_lithium.* TO 'smm'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

### 8.3 配置 .env 中的 MySQL 字段

```bash
vim /root/smm-lithium-collector/.env
```

```ini
# MySQL（启用自动同步）
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=smm
MYSQL_PASSWORD=你的密码
MYSQL_DATABASE=smm_lithium
MYSQL_AUTO_SYNC_AFTER_COLLECTION=true
```

### 8.4 同步历史数据

```bash
cd /root/smm-lithium-collector

# 先预览
.venv/bin/python scripts/sync_to_mysql.py --full --dry-run

# 全量同步
.venv/bin/python scripts/sync_to_mysql.py --full

# 或按日期范围同步
.venv/bin/python scripts/sync_to_mysql.py --start-date 2025-11-01 --end-date 2026-08-12
```

### 8.5 验证 MySQL 数据

```bash
sudo mysql -u root smm_lithium -e "
  SELECT price_date, COUNT(*) as cnt
  FROM smm_price_records
  GROUP BY price_date
  ORDER BY price_date DESC
  LIMIT 10;
"
```

### 8.6 数据流架构

```
本机 Windows                云服务器 Ubuntu
────────────                ─────────────────────
smm_lithium.db ──scp──▶   SQLite
                              │
                         sync_to_mysql.py (每次采集后自动运行)
                              │
                              ▼
                          MySQL 127.0.0.1:3306
                          ├── smm_price_records
                          ├── smm_data_quality_issues
                          └── smm_sync_runs
```

日常采集完成后（crontab 9:00），MySQL 同步自动触发（`MYSQL_AUTO_SYNC_AFTER_COLLECTION=true`）。

### 8.7 使用百度云 RDS（可选）

如果不想在服务器上自己装 MySQL，可以使用百度云的云数据库 RDS：

1. 百度云控制台 → 云数据库 RDS → 创建 MySQL 实例
2. 记下内网地址（如 `rds-xxx.mysql.bj.baidubce.com`）
3. 在 `.env` 中填入：

```ini
MYSQL_HOST=rds-xxx.mysql.bj.baidubce.com
MYSQL_PORT=3306
MYSQL_USER=smm
MYSQL_PASSWORD=你的密码
MYSQL_DATABASE=smm_lithium
```

> 使用 RDS 时需要在 RDS 白名单中添加云服务器的内网 IP。

---

## 九、ngrok 内网穿透（可选）

如果服务器没有公网 IP，或想用 HTTPS 域名，可安装 ngrok：

### 7.1 安装

```bash
# 下载 Linux 版 ngrok
wget https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz
tar xzf ngrok-v3-stable-linux-amd64.tgz
mv ngrok /root/smm-lithium-collector/ngrok
chmod +x /root/smm-lithium-collector/ngrok

# 配置 token（从 https://dashboard.ngrok.com/get-started/your-authtoken 获取）
/root/smm-lithium-collector/ngrok config add-authtoken 你的ngrok_token
```

### 7.2 设为 systemd 服务

```bash
sudo bash scripts/setup_systemd.sh    # 自动检测 ngrok 并安装
```

安装后 `.env` 中 `FILE_HOST` 留空即可 — 钉钉通知会自动通过 ngrok 本地 API 获取公网 URL。

---

## 八、登录过期处理

钉钉收到「登录状态失效」通知时：

```bash
# 在本地 Windows 上重新登录
C:\科研\smm_lithium_collector\.venv\Scripts\python.exe scripts/manual_login.py

# 上传新 Cookie 到服务器
scp C:\科研\smm_lithium_collector\data\auth\storage_state.json \
    root@你的服务器IP:~/smm-lithium-collector/data/auth/

# 在服务器上验证
bash scripts/run_daily.sh --dry-run
```

---

## 九、日常运维命令

```bash
# 查看当天日志
tail -f logs/collector_$(date +%Y-%m-%d).log

# 查看定时任务日志
tail -f logs/cron.log

# 手动补跑某天
bash scripts/run_daily.sh --date 2026-07-25

# 重启文件服务器
sudo systemctl restart smm-fileserver

# 查看文件服务状态
sudo systemctl status smm-fileserver

# 查看数据库
sqlite3 data/database/smm_lithium.db "SELECT price_date, COUNT(*) FROM lithium_spot_prices GROUP BY price_date ORDER BY price_date DESC LIMIT 5"

# 查看定时任务
crontab -l

# 查看/管理定时任务
bash scripts/install_cron.sh --dry-run   # 预览
bash scripts/install_cron.sh --remove    # 移除

# 查看/管理 systemd 服务
sudo bash scripts/setup_systemd.sh --status   # 查看状态
sudo bash scripts/setup_systemd.sh --remove   # 移除所有 SMM 服务

# 磁盘空间检查
du -sh data/ logs/
```

---

## 十、常见问题

### Q: Playwright 报错 "Executable doesn't exist"
```bash
playwright install chromium
playwright install-deps chromium
```

### Q: 截图中文乱码
```bash
sudo apt install -y fonts-noto-cjk fonts-noto-color-emoji
```

### Q: 文件服务器无法从外网访问
1. 检查百度云安全组是否开放 8888 端口
2. 检查服务器防火墙: `sudo ufw status`
3. 检查服务是否运行: `sudo systemctl status smm-fileserver`
4. 检查公网 IP: `curl -s ifconfig.me`

### Q: Cron 任务没有执行
```bash
# 查看 cron 日志
grep CRON /var/log/syslog | tail -20

# 确认 crontab 语法
crontab -l

# 确保 run_daily.sh 有执行权限
chmod +x scripts/run_daily.sh
```

### Q: 登录态频繁过期
SMM Cookie 有效期通常 7-30 天。如果频繁过期，检查：
- 是否在服务器和本机同时登录了同一个 SMM 账号（会互相踢下线）
- 考虑使用专用子账号给采集任务

---

## 部署完成检查清单

- [ ] 服务器能 SSH 登录
- [ ] `python3.11 --version` 正常
- [ ] `playwright install chromium` 成功
- [ ] `.env` 已填写 SMM_URL + 钉钉 Webhook + FILE_HOST
- [ ] `storage_state.json` 已上传
- [ ] `--dry-run` 试运行成功
- [ ] 文件服务器 8888 端口外网可访问
- [ ] crontab 已配置
- [ ] 钉钉群收到测试消息
