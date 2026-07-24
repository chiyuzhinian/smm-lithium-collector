# 公网服务器部署指南

## 推荐方案：Linux 云服务器

最经济实惠的方案，阿里云/腾讯云轻量应用服务器 2核2G 约 68元/月。

---

## 一、服务器环境准备

```bash
# Ubuntu 22.04 / CentOS 7+

# 1. 安装 Python 3.11+
sudo apt update && sudo apt install -y python3.11 python3.11-venv python3-pip

# 2. 安装 Chromium 依赖（Playwright 需要）
sudo apt install -y libnss3 libnspr4 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
  libgbm1 libasound2 libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 \
  libgtk-3-0 libpango-1.0-0 libcairo2

# 3. 克隆项目
git clone https://github.com/chiyuzhinian/smm-lithium-collector.git
cd smm-lithium-collector

# 4. 创建虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium
```

---

## 二、配置文件

```bash
# 创建 .env
cp .env.example .env
vim .env
```

填写内容：

```env
# SMM 配置（必填）
SMM_LOGIN_URL=https://user.smm.cn/login
SMM_TARGET_URL=https://new-energy.smm.cn/new_energy/14042
SMM_HEADLESS=true
SMM_TIMEOUT=30000

# MySQL（可选，不需要则留空）
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=
MYSQL_DATABASE=smm_lithium
MYSQL_AUTO_SYNC_AFTER_COLLECTION=false

# 钉钉通知（必填）
DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxx
DINGTALK_SECRET=SECxxx

# 文件下载地址（公网服务器IP或域名）
FILE_HOST=http://你的公网IP:8888
```

---

## 三、首次登录（重要）

公网服务器没有 GUI，需要从本地上传登录态：

### 方案A：本地登录后上传

```bash
# 在本地 Windows 上已完成登录，上传 storage_state.json 到服务器
scp C:\科研\smm_lithium_collector\data\auth\storage_state.json \
    user@你的服务器IP:~/smm-lithium-collector/data/auth/
```

### 方案B：服务器上 VNC 登录

```bash
# 安装轻量桌面
sudo apt install -y xvfb fluxbox x11vnc
# 启动虚拟显示器
Xvfb :99 -screen 0 1280x720x24 &
export DISPLAY=:99
# 运行手动登录
python scripts/manual_login.py
```

---

## 四、启动文件服务器

```bash
# 后台启动（端口 8888）
nohup .venv/bin/python scripts/file_server.py > logs/file_server.log 2>&1 &

# 或用 systemd 守护
sudo tee /etc/systemd/system/smm-fileserver.service << 'EOF'
[Unit]
Description=SMM File Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/smm-lithium-collector
ExecStart=/root/smm-lithium-collector/.venv/bin/python scripts/file_server.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable smm-fileserver --now
```

---

## 五、配置定时任务

```bash
# 每天 9:00 自动采集
crontab -e

# 添加以下行
0 9 * * * cd /root/smm-lithium-collector && .venv/bin/python scripts/run_daily.py >> logs/cron.log 2>&1
```

---

## 六、公网访问配置

### 开放端口

```bash
# 云服务器安全组：开放 8888 端口（TCP）

# 服务器防火墙
sudo ufw allow 8888/tcp        # Ubuntu
# 或
sudo firewall-cmd --add-port=8888/tcp --permanent  # CentOS
```

### 验证

```bash
# 浏览器访问
http://你的公网IP:8888/
```

---

## 七、测试运行

```bash
# 试运行
cd /root/smm-lithium-collector
.venv/bin/python scripts/run_daily.py --dry-run

# 正式运行
.venv/bin/python scripts/run_daily.py
```

---

## 八、日常维护

```bash
# 查看日志
tail -f logs/collector_$(date +%Y-%m-%d).log

# 手动同步 MySQL
.venv/bin/python scripts/sync_to_mysql.py --full

# 登录过期时重新上传 storage_state.json（从本地）
scp data/auth/storage_state.json user@服务器:~/smm-lithium-collector/data/auth/
```

---

## 九、成本估算

| 方案 | 配置 | 月费 |
|------|------|------|
| 阿里云轻量 | 2核2G 50G | ~68元 |
| 腾讯云轻量 | 2核2G 50G | ~65元 |
| 华为云 | 2核2G | ~88元 |

---

## 十、与本地部署的区别

| | 本地 Windows | 云服务器 Linux |
|------|:---:|:---:|
| 开机自启 | Windows Task Scheduler | cron / systemd |
| 浏览器 | 可见 Chromium | 无头 Chromium (headless) |
| 登录 | 手动打开浏览器 | 从本地上传 storage_state.json |
| 定时任务 | 9:00 自动 | 9:00 cron |
| 文件访问 | 局域网 | 公网直接下载 |
