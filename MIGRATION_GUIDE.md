# 服务器迁移与部署指南（SMM 锂电现货价格采集系统）

> 编制：2026-09-29（UTC+8）
> 背景：旧服务器（公网 IP **106.12.59.96**，Ubuntu）**2026-09-30 到期**，需在新服务器完整重建。
> 本指南配套备份包（旧服务器 `/root/` 下，SHA256 见 `smm-backup-2026-09-29.sha256`）：
> - `smm-backup-2026-09-29-core.tar.gz`（~120M，核心：项目+系统状态+MySQL dump，**必下**）
> - `smm-backup-2026-09-29-raw.tar.gz`（~3G，可选：data/raw 原始采集存档）

---

## 1. 当前代码提交状态（GitHub 核实于 2026-09-29）

仓库：`git@github.com:chiyuzhinian/smm-lithium-collector.git`

| 分支 | 状态 |
|------|------|
| `feature/smm-auth-v2` | ✅ **生产分支**，已全部推送（HEAD `313637f`） |
| `main` | ⚠️ 落后于生产，**勿在 main 上操作** |
| `feature/auth-admin-dashboard` | ✅ 已推送（历史部署分支） |
| `feature/quotes-full-catalog-trends-multichart` | ✅ 已推送（历史功能分支） |
| `feature/server-migration-2026-09-29` | ✅ 本次迁移分支：目录改名合并功能 + 凭据体检脚本 + 本指南 |

### Git 之外的生产资产（⚠️ 不在 GitHub，全部打包在备份包内）

| 资产 | 路径 | 说明 |
|------|------|------|
| 采集主库 SQLite | `data/database/smm_lithium.db`（~15M） | 12600+ 条价格记录，**核心数据** |
| 报表/导出 | `data/exports/`（~48M） | 每日汇总、固定汇总、月度业务报表 |
| 原始采集存档 | `data/raw/`（~5.3G） | 按年/月/日/分类的 HTML/JSON/PNG（可省略，见 §2） |
| 旧登录态 fallback | `data/auth/storage_state.json`（+bak） | V2 已切持久 profile，此为 fallback |
| 外部价格表 | `data/external/external_prices.xlsx` | 非 SMM 来源数据，自动进报表 |
| 环境变量 | `.env`（根目录，21 项） | SMM/MySQL/钉钉/下载链接 IP，**含密钥** |
| V2 凭据 | `/etc/smm-collector/secrets.env`（0600 root） | SMM 账号密码（V2 从 .env 迁出） |
| V2 持久浏览器档案 | `/var/lib/smm-collector/browser-profile`（0700 root） | Chromium profile，含登录态 |
| V2 状态文件 | `/var/lib/smm-collector/{auth_status,collector_status}.json` | Supervisor 状态 |
| 门户账号库 | `/var/lib/smm-fileserver/auth.db`（0600 smmweb） | huayou/admin 账号、会话、运维事件 |
| systemd 单元 | `/etc/systemd/system/smm-fileserver.service` | 沙箱加固配置 |
| nginx 站点 | `/etc/nginx/sites-enabled/{ip-access,price.ldhs.online}` | 公网入口 + 域名入口 |
| MySQL 库 | `smm_lithium`（3 表，dump 在备份包内） | 与 SQLite 同步的镜像库 |

---

## 2. 备份包内容与下载

**旧服务器路径**（两个文件，按需下载）：

| 文件 | 大小 | 内容 |
|------|------|------|
| `/root/smm-backup-2026-09-29-core.tar.gz` | ~120M | 项目目录（data/ 除 raw）+ 系统级状态 + MySQL dump |
| `/root/smm-backup-2026-09-29-raw.tar.gz` | ~3G | `data/raw/` 原始采集存档（HTML/JSON/PNG 截图，诊断用） |
| `/root/smm-backup-2026-09-29.sha256` | - | 两个文件的 SHA256 校验值 |

core 包结构：

```
smm-lithium-collector/        # 完整项目目录（含 data/ logs/ config/ .env）
│                            # 不含 .venv（新服务器重建）与 .git（从 GitHub clone）
│                            # 不含 data/raw（见 raw 包）
system-state/
├── etc-smm-collector/        # secrets.env
├── var-lib-smm-collector/    # browser-profile + 状态文件
├── var-lib-smm-fileserver/   # auth.db（含 -wal/-shm）
├── smm-fileserver.service    # systemd 单元
└── nginx-sites-enabled/      # 两个站点配置
mysql/
└── smm_lithium.sql           # mysqldump 全量导出（13.9M）
```

raw 包结构：`data/raw/`（解压时直接放入项目根目录即可）。

**从本地电脑下载（服务器到期前务必完成）：**

```bash
# 本地执行（Windows PowerShell / Linux / macOS 均可）
scp root@106.12.59.96:/root/smm-backup-2026-09-29-core.tar.gz .
scp root@106.12.59.96:/root/smm-backup-2026-09-29-raw.tar.gz .    # 可选，带宽/时间允许再下
scp root@106.12.59.96:/root/smm-backup-2026-09-29.sha256 .
# 校验
sha256sum -c smm-backup-2026-09-29.sha256
```

> ⚠️ 备份包含 `.env` 与 `secrets.env`（账号密码、钉钉 webhook），请妥善保存，勿外传。
> ⚠️ 若旧服务器到期前还需在旧机工作，下载完成后 **不要删除旧机数据**，到期自然销毁即可。

---

## 3. 新服务器部署步骤

### 3.0 新服务器准备

- Ubuntu 22.04/24.04，建议 **4C8G、磁盘 ≥ 40G**（备份解压后 data 约 5.4G + MySQL 235M + venv 343M + Chromium）
- 安全组/防火墙：开放 **80**（nginx）；**8888 不直接对外**，仅经 nginx 内网转发
- 时区：`sudo timedatectl set-timezone Asia/Shanghai`（验证/哨兵按本地时间工作）

### 3.1 获取代码 + 一键部署

```bash
cd /root
git clone git@github.com:chiyuzhinian/smm-lithium-collector.git   # 需本机有 GitHub SSH key
cd smm-lithium-collector
git checkout feature/server-migration-2026-09-29   # 迁移分支（含本指南与目录改名合并）

bash scripts/deploy_server.sh          # 系统依赖 + venv + playwright + systemd 安装
.venv/bin/playwright install chromium # Chromium 浏览器
id smmweb                              # 确认门户低权限用户已建（deploy 脚本自动创建）
```

### 3.2 恢复项目数据（从备份包）

```bash
cd /root
mkdir -p /root/migration
tar xzf smm-backup-2026-09-29-core.tar.gz -C /root/migration   # 解到中转目录，避免覆盖 clone 的项目
BACKUP=/root/migration

# 覆盖恢复 data/ 与 logs/（核心：SQLite 库 + 导出报表 + 登录态 fallback）
cp -a $BACKUP/smm-lithium-collector/data/. /root/smm-lithium-collector/data/

# raw 原始存档（可选，有 raw 包时）
# tar xzf smm-backup-2026-09-29-raw.tar.gz -C /root/smm-lithium-collector   # 产生 data/raw

# .env（含密钥；注意权限 600）
cp -a $BACKUP/smm-lithium-collector/.env /root/smm-lithium-collector/.env
chmod 600 /root/smm-lithium-collector/.env

# ⚠️ 必改：.env 的 FILE_HOST（旧公网 IP → 新服务器公网 IP，钉钉日报下载链接用它拼 URL）
vi /root/smm-lithium-collector/.env
```

### 3.3 恢复系统级状态（V2 生产布局，注意属主/权限）

```bash
# 1) SMM 凭据（0700 root / 0600 文件）
cp -a $BACKUP/system-state/etc-smm-collector /etc/smm-collector
chmod 700 /etc/smm-collector && chmod 600 /etc/smm-collector/secrets.env

# 2) 持久浏览器档案 + Supervisor 状态（登录态随此迁移）
cp -a $BACKUP/system-state/var-lib-smm-collector /var/lib/smm-collector
chown -R root:root /var/lib/smm-collector && chmod 700 /var/lib/smm-collector

# 3) 门户账号库（含 -wal/-shm；先停服务保证一致性）
systemctl stop smm-fileserver
cp -a $BACKUP/system-state/var-lib-smm-fileserver /var/lib/smm-fileserver
chown -R smmweb:smmweb /var/lib/smm-fileserver && chmod 700 /var/lib/smm-fileserver
chmod 600 /var/lib/smm-fileserver/auth.db
systemctl start smm-fileserver

# 4) systemd 单元（deploy 脚本已装则跳过；装了也要核对沙箱项一致）
# cp -a $BACKUP/system-state/smm-fileserver.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now smm-fileserver
```

### 3.4 MySQL 恢复

```bash
# 1) 导入数据
mysql < $BACKUP/mysql/smm_lithium.sql

# 2) 重建应用账号（用户名/密码见 .env 的 MYSQL_USER / MYSQL_PASSWORD）
mysql -e "CREATE USER IF NOT EXISTS '<user>'@'localhost' IDENTIFIED BY '<密码>';"
mysql -e "GRANT ALL PRIVILEGES ON smm_lithium.* TO '<user>'@'localhost'; FLUSH PRIVILEGES;"

# 3) 验证
mysql -e "SELECT COUNT(*) FROM smm_lithium.smm_price_records;"   # 应与 SQLite 一致
```

### 3.5 定时任务

```bash
cd /root/smm-lithium-collector
bash scripts/install_cron.sh --dry-run   # 预览 4 条任务
bash scripts/install_cron.sh             # 安装
crontab -l                               # 核对：
# 5 9 * * 1-5  run_daily.sh（主采集，经 smm_supervisor.py）
# 30 9 * * 1-5 catchup_daily.sh（兜底补采）
# 0 10 * * 1-5 smm_health_sentinel.py（哨兵只读自检）
# @reboot      catchup_daily.sh（开机补采）
```

### 3.6 nginx + DNS + 备案

```bash
# 1) 复制两个站点配置
cp -a $BACKUP/system-state/nginx-sites-enabled/. /etc/nginx/sites-enabled/

# 2) ⚠️ ip-access 配置里写死了旧公网 IP，改为新 IP
vi /etc/nginx/sites-enabled/ip-access

# 3) 检查并重载
nginx -t && systemctl reload nginx
curl -s http://127.0.0.1:8888/health    # 门户健康检查
```

- **DNS**：`price.ldhs.online` 的 A 记录从 `106.12.59.96` 改指向**新服务器公网 IP**
- **备案**：域名入口需 ICP 备案（状态见 `DEPLOYMENT_STATUS_2026-08-19.md`）；`ip-access` 是公网 IP 直连临时入口，新 IP 生效后即可访问
- **钉钉**：webhook 不变；`.env` 的 `FILE_HOST` 改为新 IP 后，日报里的 Excel 链接自动指向新服务器

---

## 4. 部署后验证清单

```bash
cd /root/smm-lithium-collector

# 1) 测试全量通过
.venv/bin/python -m pytest -q

# 2) 门户服务
systemctl status smm-fileserver
journalctl -u smm-fileserver -n 50
curl -s http://127.0.0.1:8888/health

# 3) 数据对账（只读）
.venv/bin/python scripts/verify_data_consistency.py
.venv/bin/python scripts/verify_key_products.py

# 4) V2 登录态与凭据（不触发采集、不写库）
sudo .venv/bin/python scripts/smm_cred_check.py      # 期望 AUTH_OK
.venv/bin/python scripts/smm_health_sentinel.py      # 哨兵自检，正常不告警

# 5) 采集全链路演练（Supervisor → 网络 → 认证 → 采集 → 验证）
bash scripts/run_daily.sh

# 6) 门户页面抽查
#    http://新IP/ 每日报价（40 品种）、/trends 走势、/reports 月报下载
#    管理员账号登录 /admin 运维中心
```

| 检查项 | 通过标准 |
|--------|----------|
| pytest | 全绿（基线 253+，含迁移分支新增目录改名合并测试） |
| /health | 200 |
| 对账脚本 | SQLite ↔ MySQL ↔ 门户 API 数量一致 |
| smm_cred_check | `AUTH_OK`（持久 profile 迁移有效） |
| run_daily.sh | 退出码 0 或 2，collection_runs 今日记录存在 |
| 9 层验证 | PASS 或仅 WARNING（周更品种滞后属正常） |

---

## 5. 已知陷阱与注意事项

1. **分支口径**：生产 = `feature/smm-auth-v2`（或本次迁移分支，内容相同）；`main` 落后，勿在 main 操作。
2. **日期口径**：MySQL 同步/验证/门控一律用校准后 `data_date`，不是 `target_date`。
3. **登录态**：V2 生产 `auto_login_enabled: false`（防 SMM 风控），登录态全部靠
   `/var/lib/smm-collector/browser-profile`；迁移后若 `smm_cred_check.py` 报
   `AUTH_EXPIRED`，需 headed 手动登录或重新初始化 profile（见
   `DEPLOYMENT_STATUS_2026-09-18.md` §3）。
4. **auth.db 复制**：必须连同 `-wal`/`-shm` 一起复制（或先停服务），否则丢最新会话/事件。
5. **下载链接 IP**：`.env` 的 `FILE_HOST` 与 nginx `ip-access` 均写死旧 IP，两处都要改。
6. **8888 不对外**：门户只经 nginx 转发，安全组只开 80；systemd 沙箱
   （ProtectSystem=strict、NoNewPrivileges）在 deploy 脚本中已配置，恢复 unit 时勿弱化。
7. **备份包敏感**：含 `.env`/`secrets.env`/`auth.db`（密码哈希），仅存本地与可信服务器。
8. **数据只增不删**：采集/验证/门控均为只读检查，原始数据永不删除；恢复后勿手工改库。
9. **GitHub 仓库可见性**：建议设为 **private**（代码无凭据，但含选择器与业务配置）。
