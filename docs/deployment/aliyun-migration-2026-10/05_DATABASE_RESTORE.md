# 05 数据库恢复报告

> 日期：2026-10-12 ｜ 对象：新服务器 SQLite + MySQL + 门户账号库

## 1. SQLite 主库

| 项 | 结果 |
|----|------|
| 恢复来源 | core 包 `data/database/smm_lithium.db`（10.4 MB，最后写入 2026-09-28 09:07） |
| 恢复位置 | `/root/smm-lithium-collector/data/database/smm_lithium.db` |
| integrity_check | **ok** ✅ |
| 记录数 | **17927 条**（lithium_spot_prices） |
| price_date 范围 | `''`（历史脏数据 1 行，与 CLAUDE.md 记录一致）～ **2026-09-25** |
| collection_runs | 65 次 |
| 导出报表 | exports 2895 个文件（含固定汇总/临时快照/月度报表）✅ |
| raw 原始存档 | 8854 个文件 / 5.4G（SHA256 传输校验一致后解压恢复）✅ |
| 门户可读性 | smmweb 以 mode=ro 读 17927 行 ✅（secure_fileserver.sh ACL） |

## 2. 门户账号库 auth.db

| 项 | 结果 |
|----|------|
| 恢复来源 | core 包 `system-state/var-lib-smm-fileserver/`（主库 + -wal + -shm 三件） |
| 恢复方式 | 服务启动前整体拷贝三件套 → 属主 smmweb:smmweb、目录 0700、文件 0600 |
| integrity_check | **ok** ✅（-wal 重放成功） |
| 用户数 | 7 个（含 huayou / admin） |
| 验证 | **admin 原账号公网登录成功**（17/17 验收 PASS；密码哈希由原库验证，未重置）✅ |

## 3. MySQL

| 项 | 结果 |
|----|------|
| 服务 | mysql-server 8.0.46（Ubuntu 22.04 官方源，与 dump 版本一致）active ✅ |
| 导入前检查 | `SHOW DATABASES` 仅系统库，**无 smm_lithium** → 导入无覆盖风险 ✅ |
| dump | `mysql/smm_lithium.sql`（CREATE DATABASE + 3 表 + 数据，含 DROP IF EXISTS 仅在空库上执行） |
| 导入结果 | **smm_price_records = 17904 条** ✅（smm_data_quality_issues / smm_sync_runs 一并导入） |
| 应用账号 | 按 .env 的 MYSQL_USER/MYSQL_PASSWORD 重建（用户=root@localhost，与旧机一致；密码未输出）✅ |

## 4. SQLite ↔ MySQL 对账（备份时点既有差异，未做任何删改）

| 项 | SQLite | MySQL | 差 |
|----|--------|-------|-----|
| 价格记录 | 17927 | 17904 | 23 |

- **差异性质**：备份时点（2026-09-28）的既有差异——SQLite 有少量行未同步到 MySQL
  （可能为 09-28 最后一次运行中 MySQL 同步批次与 SQLite upsert 的时点差）。
  CLAUDE.md 记录的历史口径同样存在（12661 vs 12656）。
- **处理**：按任务书要求保留证据、不擅自补造或删除。待采集链路恢复后，由
  `MYSQL_AUTO_SYNC_AFTER_COLLECTION` 日常同步逐步对齐；必要时用
  `scripts/sync_to_mysql.py`（先 --dry-run）人工触发。

## 5. verify_data_consistency.py 对账（只读）

```
[PASS] 指标最新价 12/12    [PASS] 涨跌幅 12/12    [PASS] 7日趋势 12/12
[PASS] 涨幅榜             [PASS] 跌幅榜
[ERROR] 2026-09-25 CSV 与 DB 差 8 行（DB有CSV无）—— 备份时点既有差异
[ERROR] 2026-09-28 有导出文件但 DB 无对应行 —— 备份时点既有状态（页面日期校准 09-24 所致）
[WARNING] 176 个历史日期 DB 有数据但无每日导出 —— 历史旧数据，备份时点既有状态
```

以上 2 ERROR / 1 WARNING 均为**备份时点已存在的状态**，恢复过程未引入新差异；证据保留，不做数据删改。

## 6. 结论

三套数据库（SQLite / auth.db / MySQL）完整恢复且完整性校验通过，门户按真实数据验收成功。
剩余差异均为备份时点既有历史状态，待采集恢复后按既有同步机制自然演进。
