# 06 — 部署计划（待阶段 6 授权后执行）

## 部署内容

分支 `feature/user-product-preferences-20261026`，基线 `c00ab8b`。改动文件（8 个）：

- `src/smm_collector/web_auth.py`（SCHEMA +1 表、AuthStore +3 方法）
- `scripts/file_server.py`（2 路由 + 2 处理器 + `_filter_preference_ids`）
- `static/js/common.js` / `quotes.js` / `trends.js` / `reports.js`
- `tests/test_web_auth.py` / `tests/test_auth_gate.py`（+18 用例）
- `docs/change-records/2026-10-26/user-product-preferences/`（本目录）

## 部署通道（沿用既有 bundle 流程）

```bash
# 本地
git bundle create user-product-preferences.bundle HEAD
scp user-product-preferences.bundle root@101.37.235.13:/root/smm-lithium-collector/

# 服务器
cd /root/smm-lithium-collector
git fetch ./user-product-preferences.bundle feature/user-product-preferences-20261026
git checkout -B feature/user-product-preferences-20261026 FETCH_HEAD
systemctl restart smm-fileserver
```

## 数据库

- **零迁移操作**：`ensure_schema()` 随服务启动自动 `CREATE TABLE IF NOT EXISTS user_preferences`，
  存量 auth.db 与全新 auth.db 行为一致；无 ALTER、无版本迁移段、无数据迁移；
- 表数据由用户操作自然生成，无预置数据。

## 生效与影响

- 生效 = 重启 `smm-fileserver`（后端 + 静态前端同次生效）；
- nginx / 证书 / cron / 采集 / 钉钉日报：**零改动**；
- 回滚点：部署前当前生产 HEAD（`c00ab8b`）。

## 上线后验证点（验收清单）

1. 域名登录正常；邀请码/注册/改密不受影响；
2. 三页各选产品 → 刷新恢复 → 退出重登恢复 → admin 与普通用户互不可见；
3. 每日报价默认视图与多选模式均不显示无有效报价行；as_of 历史日期口径正确；
4. 走势默认日均价，摘要「日均价」= 区间有效点均值；最低/最高切换正常；
5. 月报预览/下载、价格对比、today 等页面回归正常；
6. 钉钉日报与 9:05 定时采集不受影响（次日确认 collection_runs success）。

## 风险

- 低。偏好读取损坏降级为默认行为；保存失败仅提示不阻塞；过滤仅在 quotes.js 内存态；
- 唯一行为变化：每日报价不再展示「暂无数据」行（预期变更）。
