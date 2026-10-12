# 01 迁移前置检查报告（Phase 0）

> 日期：2026-10-12 ｜ 范围：本地 Git 仓库 + 本地备份包（只读检查）
> 敏感凭据不写入本报告；备份包含生产密钥，全程仅本地处理，未推送任何外部服务。

## 1. Git 仓库核实

| 项 | 结果 |
|----|------|
| 本地当前分支 | `feature/server-migration-2026-09-29` ✅ |
| 本地 HEAD | `36a3c09aa37c339be530fecbfb8ca70dacc2e0ea` ✅ |
| 远端迁移分支 HEAD（fetch 后） | `36a3c09aa37c339be530fecbfb8ca70dacc2e0ea` ✅ 与任务书一致，与本地一致 |
| 生产分支 `feature/smm-auth-v2` HEAD | `313637f34a6b156134267a5d127a42b381745d8b`（与 MIGRATION_GUIDE 记载一致） |
| `main` | 落后，未使用 ✅ |
| 工作区状态 | clean |

**迁移分支 vs 生产分支差异**（`git diff --stat`）：9 个文件、+866/-4，均为迁移工具与配置：
`MIGRATION_GUIDE.md`（新增）、`scripts/smm_cred_check.py`（凭据体检，新增）、
`config/categories_portal.yaml`（+19 行：catalog_renames 目录改名合并）、
`src/smm_collector/portal_service.py`（改名合并逻辑）、`tests/test_portal_service.py`、
`static/js/quotes.js`（4 行）、CLAUDE.md、settings.yaml 的两个 .bak/.cutover 快照。
**无采集/门户业务逻辑变更，迁移分支 = 生产分支 + 迁移工具，可作为部署基线。**

## 2. 本地备份文件

| 文件 | 大小 | 修改时间 | 状态 |
|------|------|----------|------|
| `smm-backup-2026-09-29-core.tar.gz` | 83.32 MB | 2026-09-29 10:06 | ✅ 存在 |
| `smm-backup-2026-09-29-raw.tar.gz` | 3098.75 MB | 2026-09-29 19:11 | ✅ 存在 |
| `smm-backup-2026-09-29.sha256` | — | — | ❌ **不存在（如实报告，未伪造校验）** |

## 3. 备份包校验值（本地实测）

| 文件 | SHA256（实测） |
|------|----------------|
| core.tar.gz | `44D5D22A361497E060EC1ED82F456E18982FF1234FAA920D584FAAA60CFC511E` |
| raw.tar.gz | `7B9C592458686860A20D92CEEAE35664CE155DFD2802A889D49BD10F73E60956` |

⚠️ 因官方 `.sha256` 参照文件缺失，**无法对比验证传输/下载过程中是否损坏**，只能记录实测值。
tar.gz 的 gzip 完整性已在解压前校验（tar 遍历成功读出全部 4568 + 8854 个条目，未报 CRC 错误），
但解压后逐文件内容校验（特别是 3.1GB raw 包）需在服务器上恢复后做抽检。

## 4. Core 包内部结构核实（4568 个条目）

| 备份资产 | 位置（包内） | 状态 |
|----------|-------------|------|
| 完整项目源码+配置 | `smm-lithium-collector/` | ✅ 含 config/ static/ scripts/ src/ tests/，**不含 .git 与 .venv** |
| 生产 .env（21 键） | `smm-lithium-collector/.env` | ✅ SMM/MySQL/钉钉/FILE_HOST 齐备（值未输出） |
| SQLite 主库 | `data/database/smm_lithium.db`（10.4 MB，最后写入 2026-09-28 09:07） | ✅ + `.bak-20260817` |
| 历史导出报表 | `data/exports/`（2895 个文件） | ✅ 每日汇总/固定汇总/临时快照/月度报表 |
| V1 登录态 fallback | `data/auth/storage_state.json` + 3 个 .bak/.cutover | ✅ |
| 外部价格表 | `config/data/external/external_prices.xlsx` | ✅（代码实际读取路径 `config/data/external/`，与包内一致） |
| 采集日志 | `logs/`（120 个条目） | ✅ |
| 历史备份 | `data/backups/`（历史汇总/固定汇总/db 备份） | ✅ |
| SMM 凭据 V2 | `system-state/etc-smm-collector/secrets.env` | ✅ 仅 `SMM_USERNAME`/`SMM_PASSWORD` 两键（值未输出），生成于 2026-09-18 |
| 持久浏览器档案 | `system-state/var-lib-smm-collector/browser-profile/` | ✅ 完整 Chromium profile（Cookie/LocalStorage/IndexedDB） |
| V2 状态文件 | `auth_status.json`（备份时 **AUTH_OK**，2026-09-28）+ `collector_status.json`（最近成功 2026-09-28，data_date=2026-09-24，440 行） | ✅ |
| 门户账号库 | `system-state/var-lib-smm-fileserver/auth.db` + `-wal` + `-shm`（+ .bak-20260817） | ✅ **三件齐全** |
| systemd 单元 | `system-state/smm-fileserver.service` | ✅ V2 加固版（User=smmweb、ProtectSystem=strict、StateDirectory=smm-fileserver） |
| nginx 站点 | `system-state/nginx-sites-enabled/` | ⚠️ **两个条目都是符号链接**（→ `/etc/nginx/sites-available/*`），目标文件未打包，**内容无法从备份恢复，需重建** |
| MySQL dump | `mysql/smm_lithium.sql`（MySQL 8.0.46，含 CREATE DATABASE + 3 表 + 数据） | ✅ 注意：**含 `DROP TABLE IF EXISTS`，导入前必须确认目标库无现有数据** |
| MySQL 授权参考 | `mysql/app_grants.txt` | ⚠️ 仅含 root@localhost 的 grants；**应用账号（MYSQL_USER）需按 .env 重建** |

## 5. 敏感资产与 Git 跟踪状态

- `git ls-files` 核查：`.env`、`secrets.env`、`storage_state.json`、`auth.db` **均未被 Git 跟踪** ✅（.gitignore 覆盖）
- 已跟踪的敏感风险：**`SERVER_SETUP.md` 第 15 行含 SMM 登录手机号明文**（仓库为公开可见时存在泄露风险，建议仓库转 private 或脱敏，属既有问题，本次迁移顺带提醒）
- `scripts/set_smm_secrets.sh` 无硬编码凭据（交互式写入 0600），可安全保留；其注释含旧 IP 需更新
- 备份包本身含生产密钥（.env / secrets.env / auth.db / 浏览器登录态），**禁止推送 GitHub / 上传第三方**，仅存本机与目标服务器

## 6. 历史数据完整性

| 数据 | 内容 |
|------|------|
| SQLite | `smm_lithium.db` 10.4 MB（备份时约 12661 行，2025-11 ~ 2026-09-24 数据日期） |
| raw 原始存档 | `data/raw/` 8854 个文件（按分类的 HTML/JSON/PNG，含 smm_nickel 等） |
| MySQL dump | 3 表全量（smm_price_records / smm_data_quality_issues / smm_sync_runs） |
| 报表 | 2895 个 exports 文件（含月度业务报表、固定汇总及门控临时快照） |

## 7. 旧服务器 IP 硬编码清单（106.12.59.96）

| 位置 | 类型 | 处置 |
|------|------|------|
| 备份 `.env` 的 `FILE_HOST`（含 :8888 端口） | 生产配置 | Phase 4 改为新 IP，且去掉 8888 直连（改走 nginx 80） |
| 备份 nginx `ip-access`（symlink，内容缺失） | 生产配置 | 重建时写新 IP |
| `scripts/set_smm_secrets.sh` 注释 | 文档 | Phase 2 改注释 |
| `SERVER_SETUP.md` / `DEPLOY.md` / `MIGRATION_GUIDE.md` / `DEPLOYMENT_STATUS_*.md` / `CLAUDE.md` / runbook | 文档 | Phase 2 可改；不影响部署 |
| 代码（src/ static/ config/） | — | ✅ 无硬编码旧 IP |

## 8. 发现的问题与风险汇总

1. **`.sha256` 校验文件缺失** —— 无法验证备份下载完整性（如实报告，已在 §3 记录实测值）。
2. **nginx 站点配置内容未备份**（仅符号链接）—— 需重建 `ip-access` 与 `price.ldhs.online` 两个站点文件（内容简单：server + proxy_pass 127.0.0.1:8888）。
3. **`deploy_server.sh` 是旧版**：安装 root 运行的 systemd（无沙箱）、直接写旧 cron（9:00+11:00，与当前 V2 的 9:05/9:30/10:00/@reboot 不一致）、提示开放 8888 公网、百度云文案。**不可直接执行**，Phase 2 必须修复。
4. **SMM 登录态风险**：备份时 AUTH_OK（2026-09-28），距今已 14 天（SMM Cookie 通常 7~30 天）；且旧服务器的 TurboVNC 人工登录通道已随服务器到期消失，**新服务器需重建人工登录环境**（Phase 6 专项，运行 smm_cred_check.py 前须评估并获确认）。
5. **MySQL dump 含 DROP TABLE** —— 导入前必须确认新服务器无同名库/表。
6. 本地 Windows 无法跑全量 pytest（`fcntl` 模块 Linux 专用，collection 阶段即中断）——测试验证在服务器 venv 内进行，属环境限制非代码问题。
7. `file_server.py` 监听 `0.0.0.0:8888` —— 与「8888 不对公网」目标冲突，Phase 2 改为绑定 127.0.0.1 并经 nginx 代理（钉钉链接用 FILE_HOST 指向 nginx 入口即可，notifier 已核实路径拼接方式）。
8. 文档笔误：CLAUDE.md 称 external_prices.xlsx 在 `data/external/`，实际代码与备份均为 `config/data/external/`；`.env.example` 缺 `FILE_HOST` 键。

## 9. 结论

**本地项目与备份包满足迁移条件**：代码基线明确、数据齐全（SQLite/exports/raw/MySQL dump/auth.db 三件套/浏览器 profile 全在）、凭据随包。4 个需修复/重建项（deploy 脚本、nginx 配置、file_server 绑定、旧 IP 清单）均可在 Phase 2 于新分支完成。**下一步：等待新服务器 SSH 访问配置后执行 Phase 1 服务器只读审计。**
