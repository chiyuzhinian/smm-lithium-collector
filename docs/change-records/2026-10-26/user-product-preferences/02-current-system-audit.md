# 02 — 现状审计（开发前只读审计结论）

日期：2026-10-26 ｜ 基线：`feature/price-domain-dingtalk-report-2026-10` @ `c00ab8b`（工作区干净，仅 2 个未跟踪 bundle）

## 前端三页产品选择现状

| 页 | 状态对象 | 初始选择 | ID 空间 |
|---|---|---|---|
| 每日报价 quotes.js | `qstate.picked`（L16） | 空 = 默认 40 条 SMM 业务品种视图 | **catalog id**（`cp_`+12hex，portal_service.py:800-808） |
| 价格走势 trends.js | `tstate.selected`（L35） | 空 = 引导；支持 URL `?ids=`（L72-78） | 业务稳定 id（business_products.yaml `id`） |
| 数据与报表 reports.js | `rstate.ds.picked`（L7） | 空 = 全部 40 业务品种对比 | 业务稳定 id |

- 三页均为**内存状态**，全站零 localStorage/sessionStorage——刷新即丢；
- 无任何既有偏好持久化实现（grep preference/偏好 零命中）；
- 最近先例 = 月报默认产品清单（**全局、非按用户**）：表 `monthly_default_products`
  （web_auth.py:161-166）→ AuthStore 方法（L356-371）→ GET（file_server.py:2091-2098）/
  POST admin+CSRF（L2100-2133）→ 前端 `API.post`（reports.js:321-333）→ 测试 TestMonthlyDefaultsApi
  （test_auth_gate.py:647-718）。本次新功能整套复用该模式，仅加 user 维度。

## 鉴权链（file_server.py / web_auth.py）

- 会话：HttpOnly cookie `smm_session` → `_session_gate`（L968-1024）→ `self._user`（含 id/username/role）；
- **身份只取 `self._user["id"]`，前端不传 user_id**，无横向越权面；
- POST 一律 `_check_csrf`（`X-CSRF` 头，api.js 自动获取注入）；auth.db 故障一律 503 不故障开放。

## 有效报价语义（portal_service.py）

- 业务模式 `/api/portal/quotes`：`quote_for_point`（L306-311）——`average_price` 解析非空才返回 quote，
  无报价 = **`quote: null`**；`latest_quote` 取 ≤as_of 最新有效点，price_date 恒为真实报价日；
- **catalog 模式 `/api/portal/catalog/quotes`（L955-1011）：未走 quote_for_point**，空日均价行返回
  `quote: {average_price: null, ...}` 对象而非 null ——前端过滤谓词需按「quote 存在且 average_price 非空」判定（两模式统一）。

## auth.db 建表机制

- `SCHEMA`（web_auth.py:105-192）全 `CREATE TABLE IF NOT EXISTS`；`ensure_schema()`（L217-224）
  启动时 executescript + PRAGMA table_info 补列迁移（is_deleted 先例）；**无版本号机制**；
- 新表直接追加 SCHEMA 即幂等，重启即得表，无需迁移段。

## 测试与部署

- 两层测试：test_web_auth.py（单元，tmp_path）+ test_auth_gate.py（端到端，进程内起 file_server，
  monkeypatch 全路径，TestMonthlyDefaultsApi 为模板）；
- 生产生效 = `systemctl restart smm-fileserver`；代码通道 = git bundle → scp → 服务器 fetch/checkout；
- 文档规范 = `docs/change-records/<日期>/<kebab主题>/`（NN-*.md + ROLLBACK.md）。

## 审计发现的一个既有小问题（本次顺带修复）

quotes.js `refresh()`（原 L291-292）：`updateStatus()` 先于 `qstate.visible = qstate.rows` 执行，
`st-count` 首次渲染读到旧 visible。需求二的「先过滤再更新状态」重排顺带修复。
