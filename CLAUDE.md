# CLAUDE.md — SMM 锂电现货价格采集系统（含数据门户）

> 本文档基于 **2026-09-06 生产服务器实际运行状态**全面更新。
> 维护约定：重大变更（新功能/新表/新定时任务/部署变更）后同步更新本文件与 `DEPLOYMENT_STATUS_*.md`。

## 1. 项目简介

- **项目名称**：SMM 锂电现货价格每日采集器 (smm-lithium-collector)
- **版本**：1.0.0
- **项目目标**：自动化从 SMM（上海有色网）每日采集锂电现货页面全部可见分类的现货价格数据（约 40 个分类，覆盖锂电全产业链：上游矿 → 中游材料 → 下游电芯 → 回收），存入 SQLite，同步 MySQL，导出 Excel/CSV 报表，并通过数据门户（8888）对外展示。
- **业务背景**：锂电产业链价格跟踪需要每日采集 SMM 公开报价数据。页面分类随业务更新增减，采用动态发现而非固定列表。
- **核心使用场景**：
  - 每日自动采集（服务器 cron 工作日 9:05 + 9:30/开机兜底补采）
  - 手动指定日期/分类采集；历史回溯补采（hq.smm.cn API）
  - 数据门户：今日价格 / 历史数据 / 业务专题 / 数据质量 / 管理员运维中心
  - 登录状态过期后重新手动登录；页面结构变化时运行诊断脚本

## 2. 生产环境现状（2026-09-06 核实）

| 项 | 状态 |
|----|------|
| 服务器 | Ubuntu 云服务器，公网 IP **106.12.59.96**，项目路径 `/root/smm-lithium-collector` |
| venv | `.venv`（Python 3.12）；依赖装于 venv 内 |
| systemd | `smm-fileserver.service` — **active/running**，端口 8888，非特权用户 smmweb，沙箱加固（ProtectSystem=strict 等），StateDirectory=`/var/lib/smm-fileserver` |
| cron（root） | 由 `scripts/install_cron.sh` 管理：`5 9 * * 1-5` run_daily.sh、`30 9 * * 1-5` catchup_daily.sh、`@reboot` catchup_daily.sh |
| nginx (:80) | `ip-access`（default_server，公网 IP 临时入口）+ `price.ldhs.online` → 127.0.0.1:8888 |
| SQLite | `data/database/smm_lithium.db`：**12661 条**（2025-11 至 2026-09-03，另 1 行 price_date 为空） |
| MySQL | 本地 3306，库 `smm_lithium`：`smm_price_records` 12656 条（max 2026-09-03）、`smm_sync_runs` 19 批次全 success、`smm_data_quality_issues` 22373 条 |
| collection_runs | 46 次；最近 2026-09-04 **success**（39 分类 440 行；当日页面缺 PACK） |
| 每日验证 | 08-31 ~ 09-04 连续 **WARNING**（无 FAIL；WARNING 为周更品种滞后等非致命项） |
| 门户账号 | huayou（普通）/ admin（管理员），库 `/var/lib/smm-fileserver/auth.db`（PBKDF2-SHA256，0600，Web 不可达） |
| 钉钉 | 已配置（每日采集完成后推送日报 + Excel 下载链接） |
| Git 分支 | ⚠️ 服务器部署于 **feature/auth-admin-dashboard**（领先 origin/main **7 个提交**），生产 = 此分支，勿在 main 上操作 |
| 测试 | **249 passed**（pytest 24.3s；含 portal_service/monthly_report 23 个新测试） |
| 门户重构 | ✅ 2026-09-06 完成「业务品种映射 + 每日报价/价格走势/数据与报表」重构并**已部署生产（19:58）**；当晚 **V3 深色视觉专项重构已部署**（墨蓝石墨行情工作台，规范 `docs/portal-visual-spec-v3.md`）；**2026-09-07 V4 回归修复已部署**（趋势页去明细表+摘要行、报表双 Tab 互斥、质量/运维深色统一、202 项浏览器断言全过）；8899 预览指向 repo static/，见 `DEPLOYMENT_STATUS_2026-09-06.md` §七/§八 |

### 文档现状（哪些可信）

- ✅ `DEPLOYMENT_STATUS_2026-08-19.md` — 最新生产部署状态（nginx 入口/公网 IP/ICP 状态）
- ✅ `DEPLOY.md` / `SERVER_SETUP.md` — 服务器部署手册；`QUERIES.md` — SQL 查询参考
- ✅ `config/categories_portal.yaml` — 门户+固定汇总门控+重点产品+专题的统一配置（关键文件）
- ❌ `README.md` / `RUN_GUIDE.md` — **已过时**（Windows/ngrok 时代），勿据此操作

## 3. 技术栈

| 类别 | 技术 | 版本 |
|------|------|------|
| 编程语言 | Python | ≥3.11（生产 venv 为 3.12） |
| 浏览器自动化 | Playwright (Chromium) | ≥1.45, <2 |
| HTML 解析 | BeautifulSoup4 + lxml | bs4≥4.12, lxml≥5.2 |
| 数据处理 | pandas, openpyxl | pandas≥2.2, openpyxl≥3.1 |
| 数据存储 | SQLite + MySQL (pymysql) | 标准库 / pymysql |
| 配置 | PyYAML + python-dotenv | PyYAML≥6.0, dotenv≥1.0 |
| HTTP | httpx | ≥0.27 |
| Web 服务 | http.server（标准库，scripts/file_server.py） | 零框架 |
| 测试 | pytest + pytest-asyncio | pytest≥8.2 |
| 日志 | logging (标准库) | - |

## 4. 目录结构

```
/root/smm-lithium-collector/
├── .env / .env.example          # 环境变量（SMM 登录、MySQL、钉钉；.env 不入库）
├── pyproject.toml / requirements.txt
├── CLAUDE.md                    # 本文件
├── DEPLOY.md / SERVER_SETUP.md / QUERIES.md / DEPLOYMENT_STATUS_2026-08-19.md
├── README.md / RUN_GUIDE.md     # ⚠️ 过时（Windows 时代），勿参考
│
├── config/
│   ├── settings.yaml            # 采集参数 + 附加数据源（铝/铜/镍）+ 9 层验证阈值
│   ├── selectors.yaml           # SMM 页面选择器（section 模式，分类名选择器已确认）
│   ├── categories_portal.yaml   # 门户与门控统一配置：40 规范分类/A-F分组/12指标卡/
│   │                            #   20重点产品(db三元组)/4专题/账号阈值/健康阈值/日志白名单
│   ├── business_products.yaml   # ⭐ 业务品种→DB报价系列映射（49行：40 SMM + 9 非SMM）
│   │                            #   稳定ID/组织/属性/类别/精确三元组/别名/映射状态与依据
│   │                            #   首页/走势/月报共用（2026-09-06 门户重构核心）
│   └── business_report_mapping.yaml  # 规范日报(11列)材料映射（旧口径，重构后仅供参考）
│
├── src/smm_collector/           # 核心包
│   ├── main.py                  # collect() 全流程编排 + cli()
│   ├── config.py                # AppConfig + load_config + load_portal_config
│   ├── browser.py / authentication.py
│   ├── category_navigator.py    # 分类发现/切换 + exhaust_page 滚动
│   ├── parser.py / network_capture.py / cleaner.py
│   ├── validator.py             # validate_row + run_status + modal_price_date(页面日期校准)
│   ├── database.py              # SQLite：lithium_spot_prices + collection_runs
│   ├── exporter.py              # 每日 Excel/CSV + 固定汇总门控 + update_summaries
│   ├── additional_sources.py    # 附加数据源（SMM铝/铜/镍现货，升贴水表已排除）
│   ├── business_report.py       # 规范日报生成（映射配置 → 11 列报表）
│   ├── daily_validation.py      # 每日 9 层数据验证（只读，写 logs/validation/）
│   ├── data_quality.py          # 质量报告 JSON + 每日数据状态 manifest
│   ├── mysql_database.py / synchronizer.py  # MySQL 建表/批量 upsert/重试/同步编排
│   ├── notifier.py              # 钉钉日报推送
│   ├── web_auth.py              # 门户账号/Session/CSRF/防爆破（标准库 PBKDF2）
│   ├── portal_service.py        # ⭐ 业务品种数据服务层（每日报价/走势/月报共用）：
│   │                            #   as_of 查询、跨分类去重、改名对合并、月均价+完整性+环比
│   ├── monthly_report.py        # ⭐ 华友月度业务报表 Excel（49行主表+报价明细+计算说明）
│   ├── ops_monitor.py           # 管理员运维中心聚合（/proc + 既有产物，零新数据）
│   ├── ops_events.py            # 运维事件写入 auth.db（采集器与 Web 共用）
│   └── logger.py                # 双文件日志（全量 + 仅错误）+ 控制台
│
├── scripts/
│   ├── build_monthly_report.py  # ⭐ 月度业务报表 CLI（--month 2026-08 → data/exports/月度业务报表/）
│   ├── run_daily.py             # 每日采集入口（调 main.cli）
│   ├── run_daily.sh             # cron 包装：venv 检查 + flock 互斥锁
│   ├── catchup_daily.sh         # 兜底补采（9:30 + @reboot）：查 collection_runs，今日未 success 才采集
│   ├── install_cron.sh          # 安装/移除 cron（--dry-run / --remove）
│   ├── deploy_server.sh         # Ubuntu 一键部署（venv/playwright/依赖）
│   ├── setup_systemd.sh / secure_fileserver.sh  # systemd 安装 / smmweb 最小只读权限
│   ├── file_server.py           # 数据门户 Web 服务（8888，静态页 + ~20 个 API）
│   ├── init_auth.py             # 门户账号库初始化/重置（huayou/admin，首登强制改密）
│   ├── manual_login.py / manual_login_auto.py / manual_login_metals.py  # 手动登录
│   ├── inspect_page.py          # 页面诊断（分类发现/HTML/截图/网络捕获）
│   ├── backfill_history.py      # 历史回溯：hq.smm.cn API 拉最近 30 交易日（已实现）
│   ├── backfill_remaining.py    # 缺失分类一次性补采
│   ├── backfill.py              # 桩实现（勿用）
│   ├── retry_metals.py          # 铜铝镍 11:00 延迟补采（⚠️ 当前 crontab 未安装）
│   ├── rebuild_summaries.py     # 从 SQLite 全量重建历史/固定汇总（--dry-run/--fix-gaps）
│   ├── build_market_report.py   # 市场价格报表（匹配模板格式 + 涨跌基准）
│   ├── generate_report.py       # 领导汇报报告（产业链全景 Excel）
│   ├── run_validation.py / verify_data_consistency.py / verify_key_products.py  # 验证/对账
│   ├── sync_to_mysql.py         # MySQL 同步 CLI（--date/--full/--dry-run）
│   ├── probe_api.py / probe_hq.py / probe_hq2.py / extract_mapping.py  # hq.smm.cn 探测工具
│   └── *.bat / *.ps1 / *.vbs    # Windows 时代遗留（服务器上不用）
│
├── static/                      # 门户前端（2026-09-06 重构）：
│   │                            #   index=每日报价 / trends=价格走势 / reports=数据与报表
│   │                            #   （旧）today/history/topics/quality/admin + login/register/account/403
│   │                            #   css/app.css=新设计系统；js/common/quotes/trends/reports.js=新版逻辑
├── tests/                       # 226 个测试（fixtures 不依赖真实网络）
├── data/                        # gitignore
│   ├── auth/                    # storage_state.json（合并了锂电+基础金属登录态）
│   ├── database/                # smm_lithium.db
│   ├── raw/                     # 按 年/月/日/分类 存 HTML/JSON/PNG + run_metadata_*.json
│   ├── exports/                 # 2026/MM/DD/分类CSV + 每日汇总(Excel/质量报告/manifest)
│   │   ├── 固定汇总/             # 正式固定汇总 + 临时快照 + inspect.ndjson
│   │   ├── summary/             # 领导汇报/供应链报告
│   │   └── SMM锂电现货价格_历史汇总.xlsx
│   ├── backups/ / processed/ / screenshots/
│   └── external/                # external_prices.xlsx（非 SMM 来源外部数据，自动进报表）
└── logs/
    ├── collector_YYYY-MM-DD.log / error_YYYY-MM-DD.log
    ├── cron.log / cron_metals.log / task_scheduler_exit.log
    └── validation/              # 每日 9 层验证结果（不在 Web 根）
```

## 5. 核心业务流程（采集）

```
1. 加载配置            settings.yaml + selectors.yaml + categories_portal.yaml + .env
2. 启动 Chromium        注入 data/auth/storage_state.json 恢复登录态
3. 导航目标页           登录失效 → 截图+HTML 存 data/screenshots/ 并报错
4. 动态发现分类          section 模式：按 .FilterTable_categoryName__izrKr 发现 ~39-40 分类
   逐分类：exhaust_page 滚动 → parse_category_section → validate_row → 保存 HTML/JSON/截图
   分类失败不影响后续（continue_on_category_failure）
5. 页面数据日期校准      modal_price_date()：SMM 未发布当日数据时按页面数据日期（通常前一交易日）
                       对齐（max_stale_data_days=14）；校验/门控/验证/同步全用校准后 data_date
                       ⚠️ 仅当 cron 不传 --date 时校准；手动 --date 尊重显式日期不校准
6. 入库 SQLite          upsert（业务唯一键去重）；dry-run 跳过
7. 附加数据源           铝/铜/镍现货（.ant-table），升贴水表已排除；失败不影响主流程
8. 导出 + 固定汇总门控   export_daily（每日 Excel/CSV）；固定汇总门控：规范分类全集完整 +
                       日期对齐率≥0.6 + invalid≤0.05，不达标只写临时快照不覆盖正式文件
9. 每日 manifest        SMM数据状态_{日期}.json 无条件生成（缺失分类/对齐率/门控决策）
10. 每日 9 层验证        daily_validation.py（只读）：文件/结构/日期/重复/分类/数值/波动/
                        历史连续/固定汇总一致性 → PASS/WARNING/FAIL 写 logs/validation/
11. MySQL 同步          按校准后 data_date 同步（不是 target_date，避免空窗）；3 次重试
12. 钉钉通知            日报摘要 + Excel 下载链接；运维事件写 auth.db
13. 退出码              0 success / 2 partial_success / 1 failed
```

## 6. 数据结构

### 采集数据行（parser 输出）

19 字段：`source / market / category / product_name / specification / min_price / max_price /
average_price / change_value / unit / price_date / price_date_raw / collected_at / source_url /
collection_method / raw_text / extra_fields / record_hash(SHA256) / validation_status / validation_message`

### 数据库唯一键

```
(source, market, category, product_name, specification, unit, price_date)
```

### SQLite 表

- **lithium_spot_prices** — 价格主表（12661 条）
- **collection_runs** — 采集运行元数据（schema 已变更）：
  `run_id TEXT PK, started_at, finished_at, target_date, status, expected_categories,
  success_categories, failed_categories, total_raw_rows, total_clean_rows, error_message`
  （JSON 数组存分类列表；查询用 target_date，不是 run_date）

### MySQL 表（库 smm_lithium）

- **smm_price_records** — 价格主表（UNIQUE KEY=record_hash，DECIMAL 金额）
- **smm_data_quality_issues** — 质量异常记录（warning/error）
- **smm_sync_runs** — 同步批次（sync_batch_id、sync_status、date_from/date_to 按校准后日期）

### 门户账号库（/var/lib/smm-fileserver/auth.db）

`users`（PBKDF2-SHA256 哈希、role、must_change_password、disabled、soft_deleted）、
`sessions`（token 只存 SHA-256）、`login_audit`、`lockout`、`ops_events`（采集器与 Web 共用）。

## 7. 运行方式（Linux 服务器）

```bash
cd /root/smm-lithium-collector

# 每日采集（cron 已装：工作日 9:05 + 9:30/@reboot 兜底；flock 防并发）
bash scripts/install_cron.sh            # 安装/更新定时任务（--dry-run 预览，--remove 移除）
bash scripts/run_daily.sh               # 手动采集（透传 --date/--category/--headed/--dry-run）
bash scripts/catchup_daily.sh --check   # 查看今日是否需兜底补采

# 手动采集变体
.venv/bin/python scripts/run_daily.py --date 2026-07-22 --dry-run
.venv/bin/python scripts/run_daily.py --category 锂金属 --headed

# 历史补采（hq.smm.cn API，最近 30 交易日）
.venv/bin/python scripts/backfill_history.py --dry-run

# 门户（systemd 管理）
systemctl restart smm-fileserver     # 门户改代码后重启
journalctl -u smm-fileserver -n 100  # 看门户日志

# 账号管理
.venv/bin/python scripts/init_auth.py        # 建库/交互建号
.venv/bin/python scripts/init_auth.py --reset  # 重置密码为 123456 并强制首登改密

# 汇总重建与校验
.venv/bin/python scripts/rebuild_summaries.py --dry-run
.venv/bin/python scripts/rebuild_summaries.py --fix-gaps
.venv/bin/python scripts/verify_data_consistency.py   # 门户数据对账（只读）
.venv/bin/python scripts/verify_key_products.py       # 重点产品 DB↔API 对账

# MySQL 同步 CLI
.venv/bin/python scripts/sync_to_mysql.py --date 2026-09-03 --dry-run
.venv/bin/python scripts/sync_to_mysql.py --full

# 测试
.venv/bin/python -m pytest -q        # 226 passed
```

### 数据门户（8888）

- 页面（重构版 2026-09-06 已部署；V3 深色工作台 2026-09-06 晚已部署）：
  - **`/` 每日报价**：40 条 SMM 业务品种，产品搜索（业务名/SMM名/缩写）+组织/类别筛选
    +as_of 日期查询（不含未来报价）；最低/最高/日均价(突出)/涨跌(±号,涨红跌绿)/单位/
    报价日期/采集更新时间；展开详情含完整业务规格+SMM 原始三元组+映射依据+口径说明
  - **`/trends` 价格走势**：单产品区间带(min-max)+指标曲线(日均价/最低/最高可切换)；
    同单位≤5 产品对比（统一日期窗口，缺报断点不补0）；图表与明细联动；7天/1月/自定义
  - **`/reports` 数据与报表**：①全部分类数据（分类/产品/日期/关键词+分页，动态分类）
    ②月度业务报表（选月预览 49 行 + 月均价/环比/完整性/说明 + Excel 下载）
  - （旧）`/today` `/history` `/topics` 保留可访问，不在主导航
  - `/quality` `/admin`（仅 admin） `/login` `/register` `/account` `/403`
- API：`/api/latest` `/api/overview` `/api/categories` `/api/files` `/api/history` `/api/trends`
  `/api/stats` `/api/topics` `/api/quality` `/api/key-products[/history]`
  **`/api/portal/*`**(products/quotes/history/monthly/monthly/download/dataset/*，登录即可)
  `/api/auth/*`(login/logout/me/csrf/register/change-password)
  `/api/admin/*`(overview/users/logs/tasks/events/errors/data-quality/audit)
  `/health`
- 服务端强制 role=user 注册；管理员用户管理：停用/启用/重置密码/软删除

## 8. 开发规范

### 文件/函数命名
- Python 模块：`snake_case.py`；测试：`test_*.py`；配置文件：`*.yaml`；脚本：`verb_noun.py`
- 公开函数 `snake_case()`、异步 `async def`、私有 `_underscore`、常量 `UPPER_CASE`

### 模块职责（保持单向依赖）
- `main.py` 只做流程编排；`parser.py` 只做 HTML→dict（不碰 DB/文件系统）；`exporter.py` 只做
  DataFrame→Excel/CSV；`config.py` 是唯一读配置入口
- **portal 统一配置**：门户文案/分组/指标/重点产品/门控阈值/健康阈值全部在
  `config/categories_portal.yaml`，前端与采集侧共用，改门户先改配置

### 异常处理
- 采集失败不中断其他分类；网络/解析错误记日志并标记 failed
- DB locked 自动重试（指数退避）；不吞异常不记录

### 日志
- `logging.getLogger("smm_collector")`；INFO 流程节点 / WARNING 非致命 / ERROR 致命
- 绝不输出密码、Cookie、Token、Authorization

### 测试
- 本地 HTML fixture，不依赖真实网络；DB 测试用 tmp_path；异步用 pytest-asyncio
- 测试文件（21 个）：parser/section_parser/cleaner/validator(2)/database(2)/exporter/
  synchronizer/category_navigator/business_report/consistency_rules/daily_validation/
  data_date/ops_monitor/web_auth/auth_gate 等

## 9. 安全约束

1. 不绕过验证（验证码/滑块/短信）；不隐藏自动化特征；不绕过付费墙
2. 不保存凭证：密码/Token 不入日志、不入 DB（密码只存 PBKDF2 哈希）
3. storage_state.json 仅本机使用，不传输不共享
4. 诊断优先：未确认 DOM 结构前不凭猜测填选择器
5. 门户：账号库 0600 且 StateDirectory 在 Web 根之外；CSRF + 防爆破 + 会话 token 哈希入库
6. 路径穿越防护（file_server 白名单）；systemd 沙箱：NoNewPrivileges/ProtectSystem=strict/PrivateTmp
7. 管理日志查看白名单：`{date}` 由服务器时钟推导，绝不接受客户端路径

## 10. 当前完成情况

### 已完成

| 功能 | 说明 |
|------|------|
| ✅ 采集全流程 | 动态发现 39-40 分类 → 解析 → 清洗 → 校验 → SQLite → 导出 |
| ✅ 页面数据日期校准 | 当日未发布按页面日期对齐（14 天上限），周末/节假日无 FAIL |
| ✅ 附加数据源 | SMM 铝/铜/镍现货（升贴水表排除）；历史补采 API（30 交易日） |
| ✅ 每日 9 层数据验证 | 只读，PASS/WARNING/FAIL，写 logs/validation/ |
| ✅ 固定汇总门控 | 规范全集+对齐率 0.6+invalid 0.05；不达标写临时快照 |
| ✅ 每日 manifest | 无条件生成，含缺失分类/对齐率/门控决策 |
| ✅ MySQL 同步 | 3 表 + record_hash 去重 + 3 次重试 + 按校准日期同步 |
| ✅ 钉钉日报 | 摘要 + Excel 下载链接 |
| ✅ 门户重构(已部署) | 业务品种映射(49行)+每日报价+价格走势+数据与报表+月度业务报表 Excel |
| ✅ 门户账号体系 | 表单登录+Session+CSRF+防爆破+注册(强制 user)+管理员用户管理 |
| ✅ 运维中心 | 系统资源/采集任务/验证结果/事件/错误日志查看 |
| ✅ 业务报告 | 规范日报（11 列，映射配置）+ 领导汇报 + 市场价格报表 |
| ✅ 兜底补采 | 9:30 重试 + @reboot 开机补采 + flock 互斥锁 |
| ✅ 汇总重建 | rebuild_summaries.py 从 SQLite 全量重建 + 缺口回补 |
| ✅ 一键部署 | deploy_server.sh + setup_systemd.sh + secure_fileserver.sh |
| ✅ 测试 | 226 个全部通过 |

### 数据现状（2026-09-06）

- SQLite：12661 条，2025-11 至 2026-09-03；MySQL 同步一致（12656 条）
- 每日约 440 行（39-40 分类）；页面数据日期 = 最近交易日（9:05 采集多为前一交易日）
- 固定汇总：历史汇总 174KB；正式固定汇总含门控快照机制
- 市场价格模板 39 项：SMM 28 项已全采；缺 15 项来自非 SMM 来源（Benchmark NCM 黑粉 3 项、
  江苏华友客户反馈 4 项、华宝三元正极 8 项）→ 放入 `data/external/external_prices.xlsx` 自动进报表

## 11. 未完成 / 已知问题

| 项 | 状态 |
|----|------|
| ⚠️ `prefer_api: true` | 未实现（采集仍是 DOM 解析；API 仅用于 backfill_history） |
| ⚠️ 自动登录 | 未实现（需先确认表单选择器；当前手动登录） |
| ⚠️ 分页翻页 | selectors.yaml 分页配置为空 |
| ⚠️ retry_metals.py | 脚本存在但当前 crontab 未安装；铜/镍晚发布由页面日期校准兜底 |
| ⚠️ PACK 分类 | 页面时有时无（2026-09-04 缺失 → 固定汇总 temp_snapshot，正常机制） |
| ⚠️ 文档 | README.md / RUN_GUIDE.md 过时（Windows/ngrok 时代） |
| ⚠️ pandas FutureWarning | 空 DataFrame concat 行为将变化（exporter.py:185） |
| ⚠️ 1 行 price_date 为空 | 历史脏数据（12661 行中 1 行） |
| ~~门户重构未部署~~ | ✅ 已解决：2026-09-06 19:58 部署生产（`systemctl restart smm-fileserver`），8888 = 新版；8899 预览保留供开发用 |
| ⚠️ LFP 储能型≥2.50/2.40 | SMM 官方已并入密度价格点，无独立报价；门户显示口径说明、月报留空（业务确认前维持现状） |
| ❌ 邮件通知 | 未实现（钉钉已替代） |

## 12. 维护注意（踩坑清单）

1. **页面日期滚动风险**：SMM 部分品种 9:30~10:30 翻日，越早补采越完整；补采触发前
   catchup_daily.sh 先查 collection_runs 当日是否 success
2. **日期口径**：MySQL 同步/验证/门控一律用校准后 `data_date`，不是 `target_date`
3. **升贴水表**：金属页面附加表已排除（0779491）；新增附加源时注意过滤非价格表
4. **产品规格改名**：SMM 曾把「压实密度」改为「粉体压实密度」——categories_portal.yaml 的
   key_products `db` 是列表，改名时追加三元组即可合并不产生断档
5. **重点产品身份**：(category, product_name, specification) 精确匹配，绝不用模糊名称匹配；
   同产品双分类收录靠 `db[].category` 钉死
6. **固定汇总门控**：PACK 缺失等分类不全时会写临时快照而非正式文件——这是机制不是 bug
7. **验证只读**：9 层验证与门控只记录不删数据；原始数据永不删除
8. **auth.db 权限**：Web 服务以 smmweb 运行，采集器（root cron）写 ops_events 到同一 auth.db，
   ops_events 异常安全（失败不影响采集）
9. **门户重启**：改 file_server.py / static / config 后 `systemctl restart smm-fileserver`
10. **登录态**：锂电页 + 基础金属页合并于 storage_state.json（manual_login_metals.py）；失效时
    采集会保存 login_expired 截图并报错
