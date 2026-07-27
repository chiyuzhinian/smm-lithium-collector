# 公网服务器部署指南

## 目标

把项目部署到云服务器，每天早上 9:00 自动采集 SMM 锂电数据，通过钉钉群发送日报和下载链接。

---

## 一、服务器选购

最低配置：**2核2G 50G 硬盘**

| 云厂商 | 产品 | 月费 |
|--------|------|------|
| 阿里云 | 轻量应用服务器 | ~68元 |
| 腾讯云 | 轻量应用服务器 | ~65元 |
| 华为云 | 云耀云服务器 | ~88元 |

操作系统选 **Ubuntu 22.04 LTS**。

---

## 二、环境安装

SSH 登录服务器后依次执行：

```bash
# 1. 更新系统
sudo apt update && sudo apt upgrade -y

# 2. 安装 Python 3.11
sudo apt install -y python3.11 python3.11-venv python3-pip

# 3. 安装 Chromium 依赖
sudo apt install -y libnss3 libnspr4 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
  libgbm1 libasound2 libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 \
  libgtk-3-0 libpango-1.0-0 libcairo2 libcups2

# 4. 克隆项目
git clone https://github.com/chiyuzhinian/smm-lithium-collector.git
cd smm-lithium-collector

# 5. 创建虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate

# 6. 安装依赖
pip install -r requirements.txt

# 7. 安装浏览器
playwright install chromium
playwright install-deps chromium
```

---

## 三、配置文件

```bash
cp .env.example .env
vim .env
```

```env
# SMM
SMM_LOGIN_URL=https://user.smm.cn/login
SMM_TARGET_URL=https://new-energy.smm.cn/new_energy/14042
SMM_HEADLESS=true
SMM_TIMEOUT=30000

# MySQL（服务器上装的话填 127.0.0.1，不需要则留空）
MYSQL_HOST=
MYSQL_USER=
MYSQL_PASSWORD=
MYSQL_DATABASE=
MYSQL_AUTO_SYNC_AFTER_COLLECTION=false

# 钉钉（从群机器人页面复制）
DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxx
DINGTALK_SECRET=SECxxx

# 文件下载（填服务器公网IP，云服务器控制台可查）
FILE_HOST=http://你的公网IP:8888
```

---

## 四、上传登录态（关键步骤）

SMM 需要登录 Cookie。在本地 Windows 上登录后，上传给服务器：

```bash
# 在服务器上创建目录
mkdir -p data/auth

# 在本地 Windows 上执行（用你的服务器 IP 替换）：
scp C:\科研\smm_lithium_collector\data\auth\storage_state.json \
    root@你的服务器IP:~/smm-lithium-collector/data/auth/
```

> Cookie 有效期通常 7-30 天。过期后重新上传即可。

---

## 五、启动文件服务器

```bash
# 测试启动
.venv/bin/python scripts/file_server.py &

# 验证
curl http://localhost:8888/
```

### 设为 systemd 服务（开机自启 + 崩溃重启）

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

---

## 六、定时任务（每天早上 9:00）

```bash
crontab -e
```

添加：

```
0 9 * * 1-5 cd /root/smm-lithium-collector && /root/smm-lithium-collector/.venv/bin/python scripts/run_daily.py >> logs/cron.log 2>&1
```

`1-5` 表示周一至周五，周末自动跳过。

---

## 七、防火墙配置

在云服务器**安全组**（控制台网页操作）开放端口：

| 端口 | 协议 | 用途 |
|------|------|------|
| 8888 | TCP | 文件下载 |
| 22 | TCP | SSH 登录 |

服务器内防火墙：

```bash
sudo ufw allow 8888/tcp
```

---

## 八、验证

```bash
# 1. 试运行
.venv/bin/python scripts/run_daily.py --dry-run

# 2. 正式运行
.venv/bin/python scripts/run_daily.py

# 3. 检查输出
ls data/exports/$(date +%Y)/$(date +%m)/每日汇总/Excel/

# 4. 测试下载链接
curl -I http://你的公网IP:8888/
```

---

## 九、登录过期处理

钉钉收到「登录状态失效」时：

```bash
# 在本地 Windows 上重新登录
C:\科研\smm_lithium_collector\.venv\Scripts\python.exe scripts/manual_login.py

# 上传新 Cookie 到服务器
scp C:\科研\smm_lithium_collector\data\auth\storage_state.json \
    root@你的服务器IP:~/smm-lithium-collector/data/auth/
```

---

## 十、日常运维命令

```bash
# 查看日志
tail -f logs/collector_$(date +%Y-%m-%d).log

# 手动补跑某天
.venv/bin/python scripts/run_daily.py --date 2026-07-25

# 重启文件服务器
sudo systemctl restart smm-fileserver

# 查看数据库
sqlite3 data/database/smm_lithium.db "SELECT price_date, COUNT(*) FROM lithium_spot_prices GROUP BY price_date ORDER BY price_date DESC LIMIT 5"

# 查看定时任务日志
tail logs/cron.log
```

---

## 部署完成检查清单

- [ ] 服务器能 SSH 登录
- [ ] `python3.11 --version` 正常
- [ ] `playwright install chromium` 成功
- [ ] `.env` 已填写 SMM_URL + 钉钉 Webhook
- [ ] `storage_state.json` 已上传
- [ ] `--dry-run` 试运行成功
- [ ] 文件服务器 8888 端口可访问
- [ ] crontab 已配置
- [ ] 钉钉群收到测试消息
