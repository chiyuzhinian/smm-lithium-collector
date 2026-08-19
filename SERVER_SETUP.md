# 服务器数据同步与 MySQL 配置操作手册

> 给服务器端 Claude 执行的 Step-by-Step 操作指南。
> **使用前请先填写下方「凭据清单」中的占位符。**

---

## ⚠️ 凭据清单（执行前必须填写）

文档中所有 `<...>` 占位符需要替换为实际值。用编辑器全局搜索替换：

| 占位符 | 说明 | 从哪里获取 |
| ------ | ---- | ---------- |
| `106.12.59.96` | 百度云服务器的公网 IP | `curl -s ifconfig.me` 或百度云控制台 |
| `18602563579` | SMM 登录手机号 | 本机 `.env` 中的 `SMM_USERNAME` |
| `<SMM_PASSWORD>` | SMM 登录密码 | 本机 `.env` 中的 `SMM_PASSWORD` |
| `<MYSQL_PASSWORD>` | MySQL root 密码 | 建议和本机 `.env` 中 `MYSQL_PASSWORD` 一致 |
| `<DINGTALK_WEBHOOK>` | 钉钉机器人 Webhook 完整 URL | 本机 `.env` 中的 `DINGTALK_WEBHOOK` |
| `<DINGTALK_SECRET>` | 钉钉机器人签名密钥 | 本机 `.env` 中的 `DINGTALK_SECRET` |

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

如果尚未上传，先在本机 Windows 执行（替换 `106.12.59.96`）：

```bash
scp "C:\科研\smm_lithium_collector\data\database\smm_lithium.db" root@106.12.59.96:/root/smm-lithium-collector/data/database/
scp "C:\科研\smm_lithium_collector\data\auth\storage_state.json" root@106.12.59.96:/root/smm-lithium-collector/data/auth/
scp -r "C:\科研\smm_lithium_collector\data\exports\*" root@106.12.59.96:/root/smm-lithium-collector/data/exports/
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
SMM_USERNAME=18602563579
SMM_PASSWORD=<SMM_PASSWORD>
SMM_LOGIN_URL=https://user.smm.cn/login
SMM_TARGET_URL=https://new-energy.smm.cn/new_energy/14042
SMM_HEADLESS=true
SMM_TIMEOUT=30000

# MySQL（本地服务器）
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=<MYSQL_PASSWORD>
MYSQL_DATABASE=smm_lithium
MYSQL_CHARSET=utf8mb4
MYSQL_CONNECT_TIMEOUT=10
MYSQL_SYNC_BATCH_SIZE=500
MYSQL_SYNC_MAX_RETRIES=3
MYSQL_SYNC_RETRY_INTERVAL=5
MYSQL_AUTO_CREATE_DATABASE=true
MYSQL_AUTO_SYNC_AFTER_COLLECTION=true

# 钉钉通知
DINGTALK_WEBHOOK=<DINGTALK_WEBHOOK>
DINGTALK_SECRET=<DINGTALK_SECRET>

# 文件下载（填服务器公网IP）
FILE_HOST=http://106.12.59.96:8888
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
ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '<MYSQL_PASSWORD>';
FLUSH PRIVILEGES;
SQL
```

---

## 步骤 4：创建 SMM 数据库

```bash
mysql -u root -p'<MYSQL_PASSWORD>' << 'SQL'
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
mysql -u root -p'<MYSQL_PASSWORD>' smm_lithium -e "
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
asyncio.run(send_dingtalk('🚀 SMM采集服务器部署成功', '服务已就绪，每个工作日10:00/12:30自动采集、11:00铜铝镍补采'))
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
- [ ] `curl http://106.12.59.96:8888/` 可外网访问
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
mysql -u root -p'<MYSQL_PASSWORD>' smm_lithium -e "
  SELECT sync_status, COUNT(*) FROM smm_sync_runs
  GROUP BY sync_status;
"

# 登录过期时，在本机重新登录后上传：
# scp "C:\科研\smm_lithium_collector\data\auth\storage_state.json" root@106.12.59.96:/root/smm-lithium-collector/data/auth/

# Cron 定时任务（由 scripts/install_cron.sh 安装管理）
# 周一至周五 10:00 全量采集 + MySQL 同步 + 钉钉通知（早采，发布早时当日入库）
# 周一至周五 11:00 铜铝镍延迟补采
# 周一至周五 12:30 全量兜底重跑（SMM 发布时间不固定，当日数据未发布则靠这次兜底）
```

---

## 门户与固定汇总运维

### 门户页面

- 首页 `/`：今日必看 / 关键指标 / 专题入口 / 最近更新 / 固定汇总状态
- 今日价格 `/today`、历史数据中心 `/history`、业务专题 `/topics`（回收链重点）、数据质量 `/quality`
- API：`/api/overview`、`/api/latest`、`/api/history`、`/api/categories`、`/api/trends`、`/api/quality`、`/api/topics`

### 重启门户

```bash
systemctl restart smm-fileserver        # 服务以 smmweb 非特权用户运行
journalctl -u smm-fileserver -n 30      # 查看日志
```

### 更新页面文案与分类映射

页面文案、业务分组（A-F）、首页指标卡、专题产品清单、门控阈值**全部**在 `config/categories_portal.yaml` 中维护（文件 mtime 变化后门户自动热加载，无需重启）。

页面出现新分类时：加入 `canonical_categories` 并归入某 `groups` 组，否则每日 manifest 的 `missing_categories` 会持续提示。

### 每日数据状态 manifest

每次采集（无论 MySQL 同步是否开启）都会生成：
`data/exports/{年}/{月}/每日汇总/SMM数据状态_{日期}.json`
含：预期/成功/失败分类数、缺失分类名单、行级质量统计、日期对齐率、当日全部 runs、固定汇总决策（`updated_formal` / `temp_snapshot` / `skipped`）与原因。

### 固定汇总更新规则

- 正式固定汇总（`固定汇总/SMM锂电现货价格_固定汇总.xlsx`）仅在当日运行**同时满足**时更新：
  1. 成功分类 ⊇ 规范分类全集（40 个）
  2. 日期对齐率 ≥ 0.6（price_date==当日 行占比；防"页面还在显示昨天价格"）
  3. invalid 行占比 ≤ 0.05
- 不满足时生成**临时快照**：`固定汇总/临时快照/SMM锂电现货价格_固定汇总_临时_{日期}.xlsx`，不覆盖正式文件。

### 从 SQLite 重建汇总（发现汇总文件异常时）

```bash
.venv/bin/python scripts/rebuild_summaries.py --dry-run      # 只打印统计与门控复核
.venv/bin/python scripts/rebuild_summaries.py                # 重建历史汇总 + 正式固定汇总
.venv/bin/python scripts/rebuild_summaries.py --fix-gaps     # 并补缺口日导出 + 刷新 manifest
```

### 安全

- 文件服务以 `smmweb` 非特权用户运行（systemd 加固：NoNewPrivileges/ProtectSystem=strict/PrivateTmp）
- 权限由 `bash scripts/secure_fileserver.sh` 维护（可重复执行）
- Web 可访问范围仅限 `data/exports` 与 `static/`，`.env`/登录态/源码不可下载

---

## 账号系统与运维中心（2026-08-13 上线）

### 账号体系

- **表单登录 + 服务端 Session**（替代原 HTTP Basic 单账号）。密码只存 PBKDF2-SHA256 哈希，
  账号库 `/var/lib/smm-fileserver/auth.db`（0600，smmweb 属主，Web 不可达）
- 账号：`huayou`（普通用户）/ `admin`（管理员）。**初始密码首次登录强制修改**
- 会话有效期：普通用户 10h / 管理员 4h（滑动续期）；改密后全部会话失效需重新登录
- 防爆破：同一账号+IP 10 分钟内失败 5 次 → 锁定 10 分钟（可配置）
- 未登录访问任何页面/API/文件下载都会被重定向到 `/login`；管理员路径 `/admin`、`/api/admin/*` 由服务端校验角色，普通用户一律 403

### 自助注册与用户管理

- **注册**：登录页「没有账号？注册账号」→ `/register`，仅需用户名（3-30 位字母/数字/下划线/连字符/中文）+ 密码（≥8 位）。
  新账号一律 `role=user` 普通用户——服务端强制，前端传入的任何角色参数都会被忽略。同 IP 每小时限注册 5 个。
- **管理员用户管理** `/admin/users`：查看用户列表（角色/状态/注册时间/最近登录）、停用、启用、
  重置密码（统一重置为 123456 并强制首登改密）、删除（软删除，记录保留但无法登录）。
  仅可操作普通用户；管理员账号与自身不可操作。
- **数据质量页** `/quality` 仅管理员可见（导航隐藏 + 服务端 403）。

### 初始化 / 重置账号

```bash
.venv/bin/python scripts/init_auth.py            # 交互输入两个账号的初始密码（幂等，已存在则跳过）
.venv/bin/python scripts/init_auth.py --reset    # 重置两个账号密码并强制首登改密
```

### systemd 单元要求

单元必须包含（`scripts/setup_systemd.sh` 模板已内置，线上单元手工比对）：

```ini
StateDirectory=smm-fileserver
StateDirectoryMode=0700
```

### 运维中心 `/admin`（仅管理员）

- 运维总览：总体状态（正常/警告/异常）+ Web 服务 / SMM 采集器 / 最新业务数据 / 数据验证 /
  固定汇总 / 磁盘 / 内存 / CPU 卡片（每卡标注最后检查时间）+ 今日采集状态 + 最近任务 + 最近异常
- 采集任务 / 数据质量（验证 9 层人类可读化）/ 系统资源 / 运行日志（白名单，回看 7 天）/
  用户管理（停用/启用/重置密码/软删除）/ 账号安全（改密 + 登录审计）
- 30 秒轮询 + 「立即刷新」；阈值与规则统一在 `config/categories_portal.yaml` 的 `auth` / `health` / `admin` / `tasks` 段
- 健康检查端点 `GET /health` 公开可用（无敏感信息），可做外部探活

### 安全提示

- 当前为 HTTP 明文访问。**长期生产建议 Nginx + HTTPS** 终止 TLS，并将
  `config/categories_portal.yaml` 中 `auth.cookie_secure` 改为 `true`
- 管理员后台仅提供查看与诊断能力，无 Shell / 任意命令 / 任意文件 / SQL 接口
- 日志轮转：当前应用日志无自动轮转，建议后续新增 `/etc/logrotate.d/smm-collector`
  （logs/*.log 按天保留 30 天）
