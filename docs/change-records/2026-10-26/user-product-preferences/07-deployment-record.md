# 07 — 生产部署记录

日期：2026-10-27 ｜ 部署人：chiyuzhinian（本地）+ 服务器 root

## 部署事实

| 项 | 值 |
|---|---|
| 服务器 | 阿里云 ECS 101.37.235.13，/root/smm-lithium-collector |
| 部署 commit | `0709eea8f2b7404fad3cd5478d992057c7458e13`（分支 feature/user-product-preferences-20261026） |
| 部署前基线 | `c00ab8b`（feature/price-domain-dingtalk-report-2026-10，工作区干净） |
| 通道 | 本地 `git bundle create ... HEAD`（13.9MB）→ scp → 服务器 `git fetch ./bundle HEAD` → `git checkout -B feature/user-product-preferences-20261026 FETCH_HEAD` |
| 备份 | `backups/prefs-deploy-20261027/auth.db.bak`（151,552 B，部署前 auth.db 原样） |
| 生效方式 | `systemctl restart smm-fileserver`；无 nginx/证书/cron 变更 |

## 部署后验证（服务端，全部通过）

1. `systemctl is-active smm-fileserver nginx` → active/active；
2. `curl http://127.0.0.1:8888/health` → ok；
3. 公网 `https://price.ldhs.online/health` → ok；`http://...` → 301 → https；`/login` → 200；
4. **auth.db 新表自动创建**：`user_preferences` 存在（ensure_schema 幂等，零手工迁移）；
5. 未登录 `GET/POST /api/user/preferences` → 401（会话闸门正常）；
6. 生产工作区新代码标记齐全（trends.js 恢复渲染 / quotes.js 过滤谓词 / common.js Prefs / reports.js 保存 / web_auth 表 / file_server 路由）；
7. **服务器 venv 全量 pytest：567 passed，0 failed**（含本次新增 18 例；覆盖登录态 API 往返、CSRF、跨用户隔离、显式清空等）。

## 待用户侧验收（浏览器，需真实账号登录）

| 项 | 说明 |
|---|---|
| 三页产品选择记忆 | 登录后各页选产品 → 刷新恢复 → 退出重登恢复 |
| 每日报价过滤 | 默认视图与多选模式不再显示「暂无数据」行 |
| 走势日均价 | 默认指标=日均价；摘要「日均价」= 区间有效点均值 |
| 钉钉日报 / 定时采集 | 下一工作日 9:05 采集 + 日报正常（cron 6 行确认在位，未变更） |

注：静态 JS 有 `Cache-Control: max-age=3600`，如浏览器缓存了旧版 JS，最长 1 小时后自然过期；
可 Ctrl+F5 强刷立即生效。

## 回滚

见 ROLLBACK.md：`git checkout -B feature/price-domain-dingtalk-report-2026-10 c00ab8b` + `systemctl restart smm-fileserver`；
auth.db 备份在 `backups/prefs-deploy-20261027/auth.db.bak`。

## 遗留

- 服务器仓库目录存在本次部署 bundle（`user-product-preferences.bundle`，未跟踪，与既往部署
  留下的 bundle 同惯例保留，可随时删除）。
