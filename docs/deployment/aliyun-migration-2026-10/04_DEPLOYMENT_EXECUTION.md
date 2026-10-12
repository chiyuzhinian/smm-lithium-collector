# 04 部署执行报告（含 Phase 2 变更说明与执行计划）

> 日期：2026-10-12 ｜ 本文件在部署过程中持续更新

## 1. Phase 2 部署前修复（已完成 ✅）

**分支**：`feature/aliyun-migration-2026-10`（基于 `feature/server-migration-2026-09-29`，回滚基线保留）
**提交**：`93a4747` fix(deploy): 迁移前部署脚本与安全修正（已推送 GitHub）

| # | 文件 | 变更 |
|---|------|------|
| 1 | `scripts/deploy_server.sh` | 移除旧版 root 运行 systemd 单元与直接安装的旧 cron（9:00/11:00 口径）；改为创建 smmweb 系统用户 + 指引 V2 流程（setup_systemd.sh / secure_fileserver.sh / install_cron.sh，均支持先预览）；防火墙提示改阿里云安全组 80/22，8888 不对公网；登录态提示改 V2 持久浏览器档案口径；部署摘要同步更新。**保留**：`.env` 仅在不存在时从模板创建（不覆盖生产配置）✅、Python 3.11 缺失时自动安装 ✅ |
| 2 | `scripts/file_server.py` | 新增 `FILE_SERVER_BIND` 环境变量，**默认绑定 127.0.0.1**（公网一律经 nginx 80 代理；本地调试可 `FILE_SERVER_BIND=0.0.0.0` 覆盖）；启动横幅显示实际绑定地址 |
| 3 | `scripts/nginx/ip-access.conf`（新增） | 公网 IP 直连入口模板（default_server + proxy_pass 127.0.0.1:8888，占位符 `{PUBLIC_IP}`）——**补偿备份包内 nginx 站点仅为符号链接、内容缺失的问题** |
| 4 | `scripts/nginx/price.ldhs.online.conf`（新增） | 业务域名入口模板（DNS/备案就绪后启用） |
| 5 | `.env.example` | 补充 `FILE_HOST` 键（无端口、经 nginx 的写法与说明） |
| 6 | `scripts/set_smm_secrets.sh` | 注释去除旧服务器 IP |
| 7 | `SERVER_SETUP.md` | 脱敏 SMM 手机号明文（2 处→占位符）；顶部标注文档过时、指向 MIGRATION_GUIDE |

**测试**：`file_server.py` 语法编译通过；本地 Windows 无法作为全量测试基准（fcntl/Unix 权限模型
导致 235 errors，与本次改动无关；生产基线 253 passed 在 Linux venv）。**全量 pytest 将在新服务器
venv 内执行并记录于本文档。**

**未改动的正确现状**：`install_cron.sh`（9:05/9:30/10:00/@reboot 四任务 + MARKER 防误删）、
`setup_systemd.sh`（V2 加固单元，与备份内生产单元一致）、`secure_fileserver.sh`（smmweb 最小只读 ACL）、
`catchup_daily.sh`/`run_daily.sh`（supervisor + flock，V2 链路）。

## 2. 部署执行计划（Phase 3-7）

### Phase 3 上传与恢复（需批准）

1. 上传 core 包（83MB）→ 服务器 `/root/migration/`（受限目录，非 Web 根），SHA256 复核
2. raw 包（3.1GB）→ 第二批上传，恢复至 `data/raw/`（原始证据完整保留）
3. 代码：服务器 `/root/smm-lithium-collector` git clone（HTTPS）→ checkout `feature/aliyun-migration-2026-10`
4. 恢复项目数据：core 包 `data/`（含 SQLite 主库+exports）、`.env`（0600）→ 项目目录
5. 系统级状态恢复：
   - `/etc/smm-collector/secrets.env`（0700/0600 root）
   - `/var/lib/smm-collector/`（browser-profile + 状态文件，0700 root）
   - `/var/lib/smm-fileserver/`（auth.db 三件套，**停服状态**下恢复，0700 smmweb，文件 0600）
6. 环境：python3.11（官方源）→ venv → pip（阿里云内网镜像）→ `playwright install chromium` + 系统依赖 + 中文字体
7. MySQL：apt mysql-server 安装 → **先确认无既有 smm_lithium 库** → 导入 dump → 按 .env 重建应用账号 → 只读验证
8. systemd：`setup_systemd.sh`（V2 单元）→ `secure_fileserver.sh` → daemon-reload → 启动
9. nginx：apt 安装 → 拷贝模板（替换新 IP）→ sites-enabled 符号链接 → `nginx -t` → reload
10. 全量 pytest + `verify_data_consistency.py` 对账

### Phase 4 配置修正

- `.env`：`FILE_HOST=http://101.37.235.13`（去 8888 端口）；MySQL 参数确认（127.0.0.1）
- 安全组（**用户控制台操作**）：放行 80；8888/3306 保持关闭
- 时区/swap：时区已正确；可选 +2G swap（Chromium 稳定性）

### Phase 5 门户恢复验证（先于采集）

/health → 登录页 → 原账号（huayou/admin）→ SQLite/MySQL 历史 → 每日报价 → 走势图 → 月报 Excel
下载 → /admin 运维中心。**以真实业务数据验收，非仅 HTTP 200。** SQLite↔MySQL 按既有同步策略
对账，差异保留证据不删改数据。

### Phase 6 采集恢复（每步需确认）

1. `smm_health.py` 只读自检（不触发登录尝试）
2. SMM 登录态评估：备份时 AUTH_OK（09-28），已过 14 天。`smm_cred_check.py` 会启动临时浏览器
   触发 SMM 风控——**运行前单独征得确认**
3. 若 AUTH_EXPIRED：新服务器人工登录环境（Xvfb/VNC，旧机 TurboVNC 已随服务器销毁）
4. 受控采集测试（`run_daily.sh`，观察 supervisor 全链路），**不做 09-29 之后缺口的大规模补采**
5. `install_cron.sh --dry-run` 预览 → 用户确认后安装

### Phase 7 公网验收（需确认）

- 新 IP 临时入口（http://101.37.235.13/）全项验收
- DNS：`price.ldhs.online` A 记录改指新 IP —— **用户确认后**操作（域名在用户 DNS 服务商处）
- 备案状态核对（阿里云要求域名备案与服务器在同一主体）

### 回滚预案（详见 08_ROLLBACK_PLAN.md，部署时编写）

- 分支回滚：服务器 checkout 回 `feature/server-migration-2026-09-29`
- 数据回滚：恢复操作全部为「新增」语义，不覆盖服务器既有文件；恢复前对目标位置现有内容先备份

## 3. 执行记录（部署过程中追加）

| 时间（CST） | 步骤 | 结果 |
|------------|------|------|
| 14:2x | 用户批准正式部署 + 允许安装本机 SSH 公钥 | ✅ |
| 14:2x | 公钥追加至 /root/.ssh/authorized_keys（0600），密钥登录验证 KEY_OK | ✅ |
| 14:26 | 创建 /root/migration（0700，非 Web 根） | ✅ |
| 14:2x | core 包上传（scp），服务器 SHA256 与本地实测一致（44d5d22a…） | ✅ |
| 14:3x | raw 包（3.1GB）上传，SHA256 一致（7b9c5924…），解压恢复 data/raw 5.4G | ✅ |
| 14:3x | apt 安装 python3.11/依赖/中文字体/mysql-server 8.0.46/nginx 1.18/acl | ✅ |
| 14:4x | GitHub 直连超时 → 改用 git bundle（13MB）上传克隆，checkout feature/aliyun-migration-2026-10 @93a4747 | ✅ |
| 14:4x | 恢复 data/ + logs/ + .env（0600）；创建 smmweb；恢复 /etc/smm-collector(0700/0600)、/var/lib/smm-collector(0700 root)、/var/lib/smm-fileserver(0700 smmweb, auth.db 0600) | ✅ |
| 14:4x | SQLite integrity ok 17927 条（max 2026-09-25）；auth.db integrity ok 7 用户 | ✅ |
| 14:4x | MySQL 导入 smm_price_records 17904 条；应用账号按 .env 重建 | ✅ |
| 14:4x | .env FILE_HOST → http://101.37.235.13（去端口） | ✅ |
| 14:5x | venv + requirements + Playwright Chromium（npmmirror 加速） | ✅ |
| 14:5x | setup_systemd.sh（V2 加固单元）+ secure_fileserver.sh（smmweb 只读 17927 行） | ✅ |
| 14:5x | nginx 站点重建（备份内仅符号链接，用 scripts/nginx 模板）+ nginx -t + reload；8888 仅 127.0.0.1 | ✅ |
| 15:0x | 公网门户验收 17/17 PASS（admin 原账号登录/页面/API/走势/月报下载/运维中心） | ✅ |
| 15:0x | 数据对账：5 PASS；2 ERROR/1 WARNING 均为备份时点既有差异（详见 05 报告） | ⚠️ 既有 |
| 15:1x | 服务器全量 pytest **466 passed**（修复 1 个时间敏感测试 + 4 个 V2 脚本 sys.path 迁移 bug：commit 5eb55d6 / d925259） | ✅ |
| 15:1x | smm_health.py 自检恢复工作（staleness=warn：今日未采集，符合预期） | ✅ |
| 15:2x | 用户确认「先 --dry-run」：SMM 登录态验证（后台执行中） | 进行中 |
