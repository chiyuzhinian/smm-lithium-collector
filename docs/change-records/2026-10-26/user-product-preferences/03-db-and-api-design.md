# 03 — 数据库与 API 设计

## 1. auth.db 新表（src/smm_collector/web_auth.py `SCHEMA` 末尾）

```sql
-- 用户产品选择偏好（三页独立、每用户独立；空数组=显式清空，无行=从未保存）
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id    INTEGER NOT NULL REFERENCES users(id),
    page       TEXT NOT NULL CHECK (page IN ('daily_quotes','price_trends','data_reports')),
    ids_json   TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, page)
);
```

- `(user_id, page)` 复合主键 = 唯一约束；(user_id 最左前缀) 覆盖按用户查询，无需额外索引；
- ids_json 存 JSON 文本（有序列表，与项目全链路 JSON 一致）；
- **无行 = 从未保存（页面默认行为）；有行且 `[]` = 显式清空**；
- `ensure_schema` 无需加迁移段（CREATE TABLE IF NOT EXISTS 幂等；与 ops_events 等表同模式）。

## 2. AuthStore 新方法（web_auth.py）

```python
PREFERENCE_PAGES = ("daily_quotes", "price_trends", "data_reports")

def _parse_pref_ids(self, raw) -> list[str] | None:
    # json.loads 失败 / 非字符串列表 → None（安全降级，绝不抛异常）

def get_user_preferences(self, user_id) -> dict[str, list[str] | None]:
    # 返回 {page: ids列表|None}；ids_json 损坏按 None 处理

def set_user_preference(self, user_id, page, ids) -> None:
    # 单事务 UPSERT：INSERT ... ON CONFLICT(user_id, page) DO UPDATE
```

## 3. API 契约（scripts/file_server.py）

### GET /api/user/preferences（登录即可，只读本人）

```json
200: {"preferences": {"daily_quotes": null|[ids], "price_trends": null|[ids], "data_reports": null|[ids]}}
503: auth.db 故障
```

### POST /api/user/preferences（登录 + CSRF）

```json
请求: {"page": "daily_quotes"|"price_trends"|"data_reports", "ids": ["..."]}
200:  {"ok": true, "page": "...", "ids": [<校验后的最终列表>]}
400:  page 非法 / ids 非数组 / 请求体无效
403:  CSRF 校验失败
```

### ids 白名单校验 `_filter_preference_ids(page, raw_ids)`

| page | 校验 | 说明 |
|---|---|---|
| daily_quotes | 格式 `^cp_[0-9a-f]{12}$` | catalog id；目录由 DB 派生随时变化，只验格式不查库；失效 id 由前端恢复时按目录过滤 + catalog_quotes 对未知 id 静默忽略兜底 |
| price_trends / data_reports | 对照 `_business_products()` 49 条业务 id 全集 | 与 monthly-defaults 同源白名单 |

- **无效 id 静默丢弃（保序去重）**，不返回 400（见 01 的偏离说明）；
- 处理器顺序照抄 `_handle_admin_monthly_defaults_post`：`_check_csrf` → `_read_json_body` → 结构校验 →
  过滤 → `set_user_preference`（sqlite3.Error → 503）→ `write_event("USER_PREFERENCES_UPDATED")`（审计异常吞掉）→ 响应；
- 路由注册：GET 在 do_GET portal 段（404 兜底前）；POST 在 do_POST（monthly-defaults 之后）。`/api/user/*` 无既有冲突。

## 4. 前端数据流（三页一致）

```
页面 init：加载产品目录（/api/portal/products 或 /api/portal/catalog/products）
         → Prefs.load(page)（GET 偏好，ttl=0 禁缓存；失败 toast + 降级 null=默认行为）
         → null：不动（页面默认）；数组：按当前目录过滤失效 id → 赋值 → 渲染
用户操作：toggle/add/remove/reset → 渲染 + 查询（不阻塞）
         → Prefs.save(page, ids)（POST，fire-and-forget；失败 toast「产品选择保存失败：…」，
            下次操作全量重存自愈；reset=显式清空保存空数组）
```

- trends 页 URL `?ids=` 优先于已存偏好，且 URL 导入不触发保存；
- 恢复流程走 silent 路径不写库；quotes 页恢复时失效 id 被丢弃且不触发回写。
