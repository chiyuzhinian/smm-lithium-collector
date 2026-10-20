# 02 现状审计（2026-10-20）

## Git 基线

- 当前分支 `feature/trends-window-2026-10`（工作区干净），HEAD `fbca17d`
- `git merge-base --is-ancestor feature/aliyun-migration-2026-10 HEAD` → YES（HEAD 完整包含生产迁移分支及全部迁移修复）
- 远程 origin = github.com/chiyuzhinian/smm-lithium-collector

## 40 种业务产品映射（权威来源）

- 配置：`config/business_products.yaml`（49 行：40 SMM + 9 非SMM）
- 产品身份 = `(category, product_name, specification)` 精确三元组；series 为列表支持改名合并（如 2026-08-03 压实密度→粉体压实密度）
- 业务类别字段 `info_category`，40 个 SMM 产品共 7 类（按模板行序首现）：基础金属(3) / 金属(2) / 金属盐(4) / 废极片(2) / 黑粉(7) / 正极材料(16) / 新电芯(6)
- 查询服务：`src/smm_collector/portal_service.py`
  - `load_products` → 49 行；`smm_products` → 40 行
  - `latest_quote(products, con, as_of, history_from)`：as_of 语义（含当日及更早的最近有效报价点，绝不返回未来价格）
  - `product_rows / merge_points / point_payload / quote_for_point`：四价（min/max/avg/change）同源同一行
- 网站接口：`GET /api/portal/quotes` → `portal_service.quote_rows_payload`（file_server.py:1976），前端 static/js/quotes.js 渲染
- 网站「品种数」= /api/portal/quotes 返回行数 = 40，与实际业务产品列表一致，**未发现统计口径不一致**

## 原有钉钉日报实现

- `src/smm_collector/notifier.py`（修改前 78 行）：
  - webhook 加签 `_signed_url()`（DINGTALK_WEBHOOK + DINGTALK_SECRET）
  - `send_dingtalk`：markdown 消息、15s 超时、**无重试**
  - `_dl_url`：`{FILE_HOST}/{YYYY}/{MM}/每日汇总/Excel/{filename}` —— 该路径在 file_server 会话闸门之内，**点击需门户登录**（302 登录页）
  - `build_report_message(meta, sync_stats)`：含 MySQL 新增/更新两行（本次删除）
- 调用链：cron → run_daily.sh → smm_supervisor.py → supervisor.run_collection → main.collect() → 末尾 `send_daily_notification(meta, sync_stats, db_path)`（main.py:225）；外围 `try/except: pass` 静默吞错
- 无防重机制、无发送状态记录

## 下载机制现状

- file_server.py 文件下载走 `_serve_file`（exports 根内，扩展名白名单），位于会话闸门之内
- PUBLIC_PATHS = {/login, /register, /health}；数据文件路径不公开
- 结论：现有钉钉「全量数据下载」链接实际需要门户账号登录后才能下载 → 本次新增公开签名下载（/dl/ 路由）

## 数据库

- SQLite `lithium_spot_prices`：唯一键 (source, market, category, product_name, specification, unit, price_date)
- upsert 对重复行（record_hash 相同）会刷新 collected_at（database.py:68-72）——「本次有效新报价」判定依赖此语义
- MySQL 同步计数 sync_stats（inserted/updated）来自 synchronizer.sync，仅退出钉钉正文，内部仍供 data_quality.generate_daily_report 使用

## 钉钉消息能力

- 机器人消息类型 markdown，官方长度上限 20KB
- 40 产品新报文实测约 3.9KB（本地合成库预览）→ 单条即可；仍实现 18KB 阈值防御性拆分
