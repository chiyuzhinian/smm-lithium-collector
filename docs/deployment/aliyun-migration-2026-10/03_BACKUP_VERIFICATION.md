# 03 备份包校验与完整性报告

> 日期：2026-10-12 ｜ 对象：`C:\project\smm-backup\` 两个备份包
> 本报告不含任何明文凭据。

## 1. 校验值

| 文件 | 大小 | SHA256（2026-10-12 本地实测） |
|------|------|-------------------------------|
| smm-backup-2026-09-29-core.tar.gz | 83.32 MB | `44D5D22A361497E060EC1ED82F456E18982FF1234FAA920D584FAAA60CFC511E` |
| smm-backup-2026-09-29-raw.tar.gz | 3098.75 MB | `7B9C592458686860A20D92CEEAE35664CE155DFD2802A889D49BD10F73E60956` |

**官方参照文件 `smm-backup-2026-09-29.sha256` 在本机不存在**（MIGRATION_GUIDE §2 声称随包提供）。
因此无法将实测值与来源值对比——不能声称「校验通过」。采用替代验证：两个 gzip 流均被 tar
完整遍历（core 4568 条目 / raw 8854 条目，未报 CRC/解压错误），gzip 层完整性成立。
恢复后将在服务器上对关键文件（SQLite 库、MySQL dump、auth.db）做内容级验证（sqlite
integrity_check、mysql 导入行数对账）。

## 2. 完整性逐项核对（对照 MIGRATION_GUIDE §1 资产清单）

| # | 资产 | 备份内位置 | 核对结果 |
|---|------|-----------|----------|
| 1 | SQLite 采集主库 | `smm-lithium-collector/data/database/smm_lithium.db` | ✅ 10.4 MB，最后写入 2026-09-28 09:07（与 collector_status.json 的 last_success 时间吻合）；另含 `.bak-20260817`（4.96 MB） |
| 2 | 报表/导出 | `data/exports/` | ✅ 2895 个文件（每日汇总、固定汇总+临时快照、全部历史数据 xlsx） |
| 3 | 原始采集存档 | raw 包 `data/raw/` | ✅ 8854 个文件（HTML/JSON/PNG，按分类目录，含基础金属 smm_nickel 等） |
| 4 | V1 登录态 fallback | `data/auth/storage_state.json` | ✅ 含 3 个 .bak/.cutover 版本 |
| 5 | 外部价格表 | `config/data/external/external_prices.xlsx` | ✅（与代码实际读取路径一致） |
| 6 | 生产 .env | `smm-lithium-collector/.env` | ✅ 21 键齐全（键名核对：SMM 6 / MySQL 12 / 钉钉 2 / FILE_HOST 1） |
| 7 | V2 凭据 secrets.env | `system-state/etc-smm-collector/secrets.env` | ✅ SMM_USERNAME + SMM_PASSWORD 两键，生成于 2026-09-18T16:20+08:00 |
| 8 | V2 持久浏览器档案 | `system-state/var-lib-smm-collector/browser-profile/` | ✅ 完整 Chromium 目录树（含 hq.smm.cn IndexedDB） |
| 9 | V2 状态文件 | `auth_status.json` / `collector_status.json` | ✅ 备份时 AUTH_OK / success（2026-09-28） |
| 10 | 门户账号库 | `system-state/var-lib-smm-fileserver/auth.db` | ✅ **主库 + -wal + -shm 三件齐全**（另有 .bak-20260817 三件），满足 WAL 一致性恢复条件 |
| 11 | systemd 单元 | `system-state/smm-fileserver.service` | ✅ V2 加固版（与仓库 `setup_systemd.sh` 模板一致：smmweb/ProtectSystem=strict/StateDirectory） |
| 12 | nginx 站点 | `system-state/nginx-sites-enabled/{ip-access,price.ldhs.online}` | ❌ **两条目均为符号链接** → `/etc/nginx/sites-available/*`，**目标文件内容未打包，无法恢复，需重建** |
| 13 | MySQL 库 dump | `mysql/smm_lithium.sql` | ✅ MySQL 8.0.46 全量 dump（CREATE DATABASE + DROP TABLE IF EXISTS + 3 表数据），app_grants.txt 仅 root grants（应用账号需重建） |

## 3. 恢复时注意事项（来自核对发现）

1. **auth.db 恢复**：必须在 smm-fileserver 服务停止状态下，将 auth.db + -wal + -shm 三件一并拷贝至 `/var/lib/smm-fileserver/`（属主 smmweb:smmweb，目录 0700、文件 0600），随后启动服务并做 `PRAGMA integrity_check`。禁止只拷主库。
2. **MySQL 导入顺序**：先 `SHOW DATABASES` 确认无既有 `smm_lithium`（dump 含 DROP TABLE IF EXISTS，会清空同名表），再导入；应用账号按 .env 的 MYSQL_USER/MYSQL_PASSWORD 重建（dump 不含该账号 grants）。
3. **浏览器 profile**：恢复至 `/var/lib/smm-collector/browser-profile`（root:root，0700），与 `config/settings.yaml` 的 `auth.profile_dir`、`src/smm_collector/browser_v2.py` 默认路径一致。恢复后不得并发打开该 profile（profile_lock 机制）。
4. **raw 包**：解压至项目根目录（产生 `data/raw/`），第二批次上传即可，但不可丢弃。

## 4. 判定

**备份包可用**。核心资产（数据/凭据/账号库/登录态/服务单元）全部在包内且状态一致；
唯一内容级缺失是 nginx 两个站点配置文件（内容简单，可重建）。
.sha256 参照文件缺失导致的不可对比问题已如实记录，以 tar 全遍历 + 恢复后内容验证作为补偿。
