# ROLLBACK — 钉钉业务报价日报升级回滚方案（2026-10-20）

## 原则

- 日报是只读展示层：**回滚绝不删除、覆盖或回滚任何已采集价格数据**（SQLite/MySQL 不动）
- 新生成的 `SMM锂电业务产品报价_*.xlsx` 为导出产物，可留可删
- 回滚后钉钉恢复旧版采集通知模板（含 MySQL 统计行），下载链接恢复登录式

## 1. Git 代码回滚（恢复旧版日报）

```bash
cd /root/smm-lithium-collector
# 方式 A：整分支回退到部署前 SHA（<PRE_SHA> 见 07 部署计划第 1 步记录）
git checkout -B feature/dingtalk-business-report-2026-10 <PRE_SHA>

# 方式 B：恢复被替换的 4 个文件（备份目录）
cp backups/dingtalk-report-pre-20261020/notifier.py   src/smm_collector/notifier.py
cp backups/dingtalk-report-pre-20261020/main.py       src/smm_collector/main.py
cp backups/dingtalk-report-pre-20261020/ops_events.py src/smm_collector/ops_events.py
cp backups/dingtalk-report-pre-20261020/file_server.py scripts/file_server.py
```

回滚后 `business_daily.py` / `dl_link.py` 不再被引用（可保留文件，无副作用）。

## 2. 新版业务报价模块停用

- 无独立服务/无 cron 变更：模块随代码回滚即停用
- 可选清理（非必需）：`rm /var/lib/smm-fileserver/dl_sign.key`（删除后 /dl/ 一律 404，新链接立即失效）

## 3. 下载功能恢复

- 旧版 `notifier._dl_url` 登录式链接随代码回滚自动恢复
- `/dl/` 路由：file_server.py 回滚后消失；即便保留代码，删除 dl_sign.key 也使其全部 404

## 4. Linux 服务恢复步骤

```bash
systemctl restart smm-fileserver   # file_server.py 回滚后必须重启
# cron / supervisor / 采集流程无需任何操作（本次未改动）
```

## 5. 数据保障

- 采集数据、collection_runs、MySQL 三表、auth.db 均未被本次改动写入
- 若回滚前已发过新版日报，无需撤回（消息为只读快照）
