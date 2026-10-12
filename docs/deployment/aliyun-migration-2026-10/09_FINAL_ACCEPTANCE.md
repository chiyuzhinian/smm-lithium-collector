# 09 最终验收报告

> 日期：2026-10-12 ｜ 新服务器：101.37.235.13（阿里云 ECS）

## 验收清单

| # | 验收项 | 结果 | 证据 |
|---|--------|------|------|
| 1 | 登录成功 | ✅ PASS | admin 原账号（备份 auth.db 哈希）公网登录 ok，/api/auth/me 身份 admin/admin |
| 2 | 首页数据正确 | ✅ PASS | /api/portal/quotes 40 条，price_date=2026-10-12 15:31 采集 |
| 3 | 历史走势图正确 | ✅ PASS | /api/portal/history 返回序列；对账 7 日趋势 12/12 与 DB 一致 |
| 4 | 多选产品显示正常 | ✅ PASS | history API 支持 ids 多产品（统一日期窗口） |
| 5 | 报表下载正常 | ✅ PASS | 月报 Excel 47.9KB 下载；/api/files 200 |
| 6 | 管理员后台正常 | ✅ PASS | /admin 200 + /api/admin/overview 200（服务端角色校验） |
| 7 | API 返回正常 | ✅ PASS | 17 项公网 API/页面断言全部 PASS |
| 8 | 历史数据库恢复成功 | ✅ PASS | SQLite 17927→18358 条（2025-11~2026-10-12，integrity ok）；MySQL 17904→18238 条；auth.db 7 用户 integrity ok |
| 9 | 采集器状态明确 | ✅ PASS | 登录态 AUTH_OK（10-12 实测）；真实采集 success（40 分类 447 行）；钉钉日报已推送；MySQL 同步 success |
| 10 | 服务器重启后服务可恢复 | ✅ PASS（2026-10-12 15:48 执行） | 重启 75 秒恢复；smm-fileserver/nginx/mysql/cron 全 active；crontab 保留；@reboot catchup 正确判定「今日已采集成功，跳过」；SQLite 18358 条不变；公网复验 17/17 |
| 11 | 全量测试 | ✅ PASS | 服务器 venv pytest **466 passed** |
| 12 | 定时任务 | ✅ PASS | cron 四条已装（9:05/9:30/10:00/@reboot） |

## 待用户人工操作/决策项

| # | 项 | 说明 |
|---|----|------|
| 1 | ~~服务器重启验收~~ | ✅ 已完成（2026-10-12 15:48） |
| 2 | **DNS 切换**（price.ldhs.online） | 用户确认暂不动，继续用公网 IP 临时入口；后续切换需在 DNS 服务商处改 A 记录 + 核查**阿里云备案**（旧备案在百度云，可能需接入备案） |
| 3 | 域名切换后启用 nginx 域名站点 | 备案/DNS 就绪后：symlink 启用 + reload（命令见 08 回滚预案第 3 节） |

## 最终交付信息

1. **Git Commit**：`feature/aliyun-migration-2026-10`，部署提交 1963e09（含 93a4747 / 5eb55d6 / d925259 四个迁移修复，均已推送 GitHub）
2. **公网访问地址**：http://101.37.235.13（登录：huayou / admin 原账号原密码）
3. **前端与 API**：全部正常（17/17）
4. **历史数据**：SQLite 18358 条（2025-11 ~ 2026-10-12，09-29~10-11 空窗）；MySQL 18238 条（差 120 条为备份时点既有差异 23 + 今日按 data_date 口径 334 与全量 428 的差额，日常同步按校准日期口径演进）
5. **SQLite/MySQL 对账**：备份时点既有差异保留证据不删改；今日同步 success
6. **SMM 登录状态**：AUTH_OK（持久档案迁移后有效）；采集链路全通
7. **Cron**：已启用（4 条）
8. **需人工操作**：重启验收、DNS/备案、域名站点启用（见上表）
9. **后续更新方式**：本地 Claude Code 修改 → commit + push GitHub → `git bundle create` → scp 到服务器 → 服务器 `git pull /root/migration/smm-repo.bundle feature/aliyun-migration-2026-10`（服务器无法直连 GitHub）
