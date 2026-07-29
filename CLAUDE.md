# CLAUDE.md — SMM Lithium Spot Price Collector

## 1. 项目简介

- **项目名称**：SMM 锂电现货价格每日采集器 (smm-lithium-collector)
- **版本**：1.0.0
- **项目目标**：自动化从 SMM（上海有色网）每日采集锂电现货页面全部可见分类的现货价格数据（约 40 个分类，覆盖锂电全产业链），存入 SQLite 数据库并导出 Excel/CSV 报表。
- **业务背景**：锂电产业链价格跟踪需要每日采集 SMM 公开报价数据，手工采集耗时易错，需要合规的自动化工具有效获取数据。页面分类会随业务更新而增减，因此采用动态发现而非固定列表。
- **核心使用场景**：
  - 每日自动采集（Windows 任务计划程序 10:00）
  - 手动指定日期/分类采集
  - 页面结构变化时运行诊断脚本（自动发现全部分类）
  - 登录状态过期后重新手动登录
  - 测试新配置/结构时使用 dry-run 模式

## 2. 技术栈

| 类别 | 技术 | 版本 |
|------|------|------|
| 编程语言 | Python | ≥3.11 |
| 浏览器自动化 | Playwright (Chromium) | ≥1.45, <2 |
| HTML 解析 | BeautifulSoup4 + lxml | bs4≥4.12, lxml≥5.2 |
| 数据处理 | pandas, openpyxl | pandas≥2.2, openpyxl≥3.1 |
| 数据存储 | SQLite | 标准库 |
| 配置 | PyYAML + python-dotenv | PyYAML≥6.0, dotenv≥1.0 |
| HTTP | httpx | ≥0.27 |
| 测试 | pytest + pytest-asyncio | pytest≥8.2 |
| 日志 | logging (标准库) | - |

## 3. 目录结构

```
C:\科研\smm_lithium_collector/
├── .env                          # 环境变量（含敏感信息，gitignore）
├── .env.example                  # 环境变量模板（可提交）
├── .gitignore                    # Git 忽略规则
├── pyproject.toml                # 项目元数据和依赖声明（setuptools）
├── requirements.txt              # pip 依赖列表（含测试依赖）
├── README.md                     # 项目使用文档（中文）
├── CLAUDE.md                     # 本文件 — AI/开发者项目上下文
│
├── config/
│   ├── settings.yaml             # 分类模式（auto/manual）+ 输出路径 + 浏览器/采集参数
│   └── selectors.yaml            # 页面 CSS 选择器 — 必须在 inspect_page.py 后人工确认填写
│
├── src/smm_collector/            # 核心 Python 包
│   ├── __init__.py               # __version__ = "1.0.0"
│   ├── main.py                   # 主入口：collect() 完整采集流程 + cli() 命令行解析
│   ├── config.py                 # AppConfig 数据类 + load_config() 合并 YAML 和 .env
│   ├── logger.py                 # setup_logging() 双文件日志（全量+仅错误）+ 控制台
│   ├── browser.py                # open_browser() / close_browser() Playwright 生命周期
│   ├── authentication.py         # save_manual_login() / looks_logged_out() / guarded_auto_login()
│   ├── category_navigator.py     # CategoryNavigator 分类切换 + exhaust_page() 滚动加载
│   ├── parser.py                 # parse_html_tables() 通用表格解析 + parse_category_section() 按区块解析
│   ├── network_capture.py        # NetworkCapture 拦截 XHR/Fetch JSON 响应
│   ├── cleaner.py                # normalize_str/unit + parse_decimal() + parse_price_date()
│   ├── validator.py              # validate_row() + check_price_volatility() + run_status()
│   ├── database.py               # Database 类：SQLite schema、upsert、save_run + 多日查询
│   ├── price_statistics.py       # 产品分组 + 近N日日期选择 + Decimal均价计算
│   ├── exporter.py               # export_daily() Excel/CSV 导出 + 近N日展示 + 历史/固定汇总
│   ├── mysql_database.py         # MySQL 连接/建表/批量upsert/异常记录/同步记录
│   ├── synchronizer.py           # SQLite→MySQL 同步编排 + 数据分类 + 重试 + 质量跟踪
│   ├── data_quality.py           # generate_daily_report() 数据质量报告 JSON
│   └── logger.py                 # setup_logging() 双文件日志（全量+仅错误）+ 控制台
│
├── scripts/                      # 独立脚本
│   ├── run_daily.py              # 每日采集入口（导入 main.cli）
│   ├── run_daily.bat             # Windows 批处理包装（激活 venv）
│   ├── manual_login.py           # 手动登录脚本 — 有界面浏览器
│   ├── inspect_page.py           # 页面诊断脚本 — 自动发现全部分类
│   ├── generate_report.py        # 领导汇报报告生成器
│   ├── sync_to_mysql.py          # MySQL 同步脚本（支持 --date/--full/--dry-run）
│   ├── backfill.py               # 历史补采脚本（当前为桩实现）
│   ├── sanitize_diagnostics.py   # 清理已保存网络诊断文件的敏感 URL 参数
│   ├── install_daily_task.ps1    # 安装 Windows 计划任务
│   ├── query_daily_task.ps1      # 查询计划任务状态
│   └── remove_daily_task.ps1     # 删除计划任务
│
├── tests/                        # pytest 测试
│   ├── fixtures/                 # HTML fixture 文件
│   │   ├── lithium_metal.html    # 锂金属：1行数据
│   │   ├── lithium_ore.html      # 锂矿：2行数据
│   │   └── lithium_compound.html # 锂化合物：2行数据
│   ├── test_parser.py            # 通用 HTML 表格解析
│   ├── test_section_parser.py    # 分类区块解析
│   ├── test_cleaner.py           # parse_decimal / parse_price_date
│   ├── test_cleaner_normalize.py # 字符串标准化
│   ├── test_validator.py         # 校验规则 + run_status
│   ├── test_validator_enhanced.py # 波动检测 + 价格范围
│   ├── test_database.py          # 去重/更新/分类唯一键
│   ├── test_database_queries.py  # 多日查询
│   ├── test_exporter.py          # 多 Sheet 导出
│   ├── test_price_statistics.py  # 产品分组 + 均价计算
│   ├── test_synchronizer.py      # 同步逻辑
│   └── test_category_navigator.py # 分类遍历 + 失败继续
│
├── data/
│   ├── auth/                     # Playwright storage_state.json（gitignore）
│   ├── database/                 # SQLite 数据库文件（gitignore）
│   ├── raw/                      # 原始采集数据 + 网络捕获 + 诊断报告（gitignore）
│   ├── processed/                # 处理后中间数据（gitignore，目前未使用）
│   ├── exports/                  # 导出 Excel/CSV/历史汇总（gitignore）
│   └── screenshots/              # 截图（gitignore）
│
└── logs/                         # 日志文件（gitignore）
```

## 4. 核心业务流程

```
┌────────────────────────────────────────────────────────────────┐
│ 1. 加载配置                                                     │
│    settings.yaml  +  selectors.yaml  +  .env                    │
└─────────────────────┬──────────────────────────────────────────┘
                      ▼
┌────────────────────────────────────────────────────────────────┐
│ 2. 启动 Chromium 浏览器                                         │
│    注入 data/auth/storage_state.json 恢复登录态                  │
└─────────────────────┬──────────────────────────────────────────┘
                      ▼
┌────────────────────────────────────────────────────────────────┐
│ 3. 导航到 SMM 锂电现货页面 (SMM_TARGET_URL)                     │
│    检测登录状态 → 失效时保存现场截图+HTML 并报错                  │
└─────────────────────┬──────────────────────────────────────────┘
                      ▼
┌────────────────────────────────────────────────────────────────┐
│ 4. 自动发现全部分类 + 按 DOM 顺序遍历采集                           │
│    ├── discover_categories(page, heading_selector) 动态发现         │
│    ├── 约 40 个分类覆盖锂电全产业链（上游矿→中游材料→下游电芯→回收）│
│    ├── CategoryNavigator.locate(name) 定位分类入口                  │
│    ├── exhaust_page() 展开"更多" + 稳定滚动                         │
│    ├── parse_html_tables() / parse_category_section() 解析表格      │
│    ├── 字段映射：中文表头 → 标准英文字段                             │
│    ├── parse_decimal() 数字清洗 + parse_price_date() 日期推导       │
│    ├── validate_row() 数据校验（价格逻辑+完整性）                    │
│    └── 分类失败不影响后续分类（continue_on_category_failure）        │
└─────────────────────┬──────────────────────────────────────────┘
                      ▼
┌────────────────────────────────────────────────────────────────┐
│ 5. 网络数据捕获                                                  │
│    拦截 XHR/Fetch JSON 响应（含价格关键词），去敏 URL 后保存      │
└─────────────────────┬──────────────────────────────────────────┘
                      ▼
┌────────────────────────────────────────────────────────────────┐
│ 6. 存储和导出                                                   │
│    ├── Database.upsert() → SQLite（去重/更新）                   │
│    ├── SQLite 查询最近 3 个价格日期                                 │
│    ├── 产品分组 + 近三日 Decimal 均价计算                          │
│    ├── export_daily() → Excel + CSV（动态分类Sheet + 中文列名）    │
│    ├── 历史汇总更新（仅 status==success，保持原字段）               │
│    └── 固定汇总更新（仅全部发现分类完整成功时）                     │
└─────────────────────┬──────────────────────────────────────────┘
                      ▼
┌────────────────────────────────────────────────────────────────┐
│ 7. MySQL 同步 + 质量报告                                         │
│    ├── synchronizer.sync() → MySQL 批量 upsert（record_hash）     │
│    ├── 写入数据质量问题表 smm_data_quality_issues                  │
│    ├── 记录同步批次 smm_sync_runs                                 │
│    ├── 支持失败重试（3次，指数退避）                               │
│    └── generate_daily_report() → JSON 质量报告                   │
└─────────────────────┬──────────────────────────────────────────┘
                      ▼
┌────────────────────────────────────────────────────────────────┐
│ 8. 写入元数据 + 退出                                            │
│    run_metadata_{stamp}.json + 退出码(0/2/1)                    │
└────────────────────────────────────────────────────────────────┘
```

## 5. 数据结构

### 采集数据行 (parser 输出)

| 字段 | 类型 | 说明 |
|------|------|------|
| `source` | str | "SMM" |
| `market` | str | "SMM锂电现货" |
| `category` | str | "锂金属" / "锂矿" / "锂化合物" |
| `product_name` | str | 品名（如"金属锂""锂辉石""碳酸锂"） |
| `specification` | str | 规格（如"Li≥99%"） |
| `min_price` | Decimal\|None | 最低价 |
| `max_price` | Decimal\|None | 最高价 |
| `average_price` | Decimal\|None | 平均价 |
| `change_value` | Decimal\|None | 涨跌 |
| `unit` | str | 单位（如"元/吨"） |
| `price_date` | date\|None | 价格日期（MM-DD 自动推导年份） |
| `price_date_raw` | str | 原始日期文本 |
| `collected_at` | datetime | 采集时间 |
| `source_url` | str | 来源URL |
| `collection_method` | str | "DOM" |
| `raw_text` | str | 原始行文本 |
| `extra_fields` | str | JSON — 未映射的额外字段（如"产地"） |
| `record_hash` | str | SHA256 业务内容哈希 |
| `validation_status` | str | "valid" / "warning" / "invalid" |
| `validation_message` | str | 校验信息 |

### 数据库唯一键

```
(source, market, category, product_name, specification, unit, price_date)
```

### 数据库表

**SQLite**（本地）：
- **lithium_spot_prices** — 价格数据主表（1634 条，覆盖 2025-11 至 2026-07）
- **collection_runs** — 每次采集的运行元数据

**MySQL**（同步备份）：
- **smm_price_records** — 价格主表（UNIQUE KEY = record_hash，DECIMAL 金额字段）
- **smm_data_quality_issues** — 数据质量异常记录（warning/error，可追溯）
- **smm_sync_runs** — 同步批次记录（running/success/partial_success/failed）

### Excel 导出字段（含近三日统计）

原有 19 列 + 新增 2 列：

| 列 | 中文名 | 说明 |
|----|-------|------|
| `three_day_average_price` | 近三日均价 | 同产品窗口内均价，仅 Excel 展示 |
| `three_day_valid_count` | 近三日有效天数 | 1~3，说明用几天数据计算的均价 |

### 导出 Excel Sheet（动态）

- `全部数据` / 40 个分类各一个 Sheet / `采集说明`

## 6. 运行方式

### 环境要求

- Windows 系统
- Python 3.11+
- Chromium（Playwright 自动下载）

### 初始化

```bat
cd /d C:\科研\smm_lithium_collector
py -m venv .venv
.venv\Scripts\activate
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
py -m playwright install chromium
copy .env.example .env
```

### 编辑 .env

必须填写：
- `SMM_LOGIN_URL` = 登录页面 URL
- `SMM_TARGET_URL` = 目标数据页面 URL

`SMM_USERNAME` 和 `SMM_PASSWORD` 仅在确认无验证码且配置了可靠表单选择器后才用于自动登录；当前安全默认是手动登录。

### 运行命令

```bat
# 首次：手动登录
python scripts/manual_login.py

# 页面诊断（结构变化时使用）
python scripts/inspect_page.py

# 每日采集
python scripts/run_daily.py

# 指定日期
python scripts/run_daily.py --date 2026-07-22

# 指定分类
python scripts/run_daily.py --category 锂金属 --headed

# 试运行（不写数据库）
python scripts/run_daily.py --dry-run

# 历史补采（需先确认页面支持）
python scripts/backfill.py --start-date 2026-07-01 --end-date 2026-07-22

# 运行测试
pytest -q

# 安装每日定时任务
powershell -ExecutionPolicy Bypass -File .\scripts\install_daily_task.ps1
```

### 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | success — 所有预期分类采集成功 |
| 2 | partial_success — 部分分类成功 |
| 1 | failed — 所有分类失败 |

## 7. 开发规范

### 文件命名
- Python 模块：`snake_case.py`
- 测试文件：`test_*.py`
- 配置文件：`*.yaml`
- 脚本：`verb_noun.py`

### 函数命名
- 公开函数：`snake_case()`
- 异步函数：`async def snake_case()`
- 私有/内部：`_underscore_prefix()`
- 常量/别名：`UPPER_CASE`

### 模块职责
- `main.py` — 只做流程编排，不包含具体的解析/存储/导出逻辑
- `parser.py` — 只做 HTML→dict 转换，不访问数据库和文件系统
- `exporter.py` — 只做 DataFrame→Excel/CSV，不采集数据
- `config.py` — 唯一读取配置文件的地方

### 异常处理
- 采集失败不应中断其他分类采集
- 网络/解析错误应记录日志并标记分类为 failed
- 数据库 locked 错误自动重试（指数退避）
- 不要吞掉异常不记录

### 日志记录
- 使用 `logging.getLogger("smm_collector")`
- INFO：流程节点（分类开始、导出路径、最终状态）
- WARNING：非致命异常（日期不匹配、平均价范围）
- ERROR：致命错误（登录失效、解析为空）
- 不要在日志中输出密码、完整 Cookie、Token、Authorization

### 类型注解
- 使用 `from __future__ import annotations`
- 公开函数应标注参数和返回值类型
- 使用 `Decimal | None` 而非 `Optional[Decimal]`

### 注释规范
- 中文注释可用于业务逻辑说明
- 英文注释用于技术细节
- 每个公开模块应有模块级 docstring
- 复杂算法应注释原因（Why），而非复述代码（What）

### 测试要求
- 使用本地 HTML fixture，不依赖真实网络
- 每个模块至少覆盖正常路径 + 一个边缘场景
- 数据库测试使用 `tmp_path`
- 异步测试使用 `pytest-asyncio`
- 不将 fixture 数据写死为真实网站结构

### 敏感信息管理
- **绝不**在代码中硬编码账号、密码、Token、Cookie、API Key
- `.env` 必须加入 `.gitignore`
- 数据目录（`data/`）整体 gitignore
- 日志输出前应过滤敏感字段
- URL 保存前应去除 query string（`network_capture.safe_url()`）
- `data/auth/` 目录 `storage_state.json` 不提交

## 8. 安全约束

1. **不绕过验证**：不破解验证码、滑块验证、短信验证
2. **不隐藏自动化**：不伪造 User-Agent、不隐藏 webdriver 特征
3. **不过度采集**：不绕过付费墙、登录限制、分页/导出上限
4. **不保存凭证**：不将密码/Token/Authorization Header 保存到日志或数据库
5. **不共享会话**：storage_state.json 仅本机使用，不传输不共享
6. **合规采集**：使用用户自己合法的 SMM 账号，采集账号正常可见的数据
7. **诊断优先**：在未确认页面实际 DOM 结构前，不凭猜测填入 CSS 选择器

## 9. 当前完成情况

### 已完成

| 功能 | 说明 |
|------|------|
| ✅ 分类自动发现 | 从页面 DOM 动态提取全部 40 个分类 |
| ✅ 完整采集流程 | 自动发现 → 逐分类解析 → 清洗 → 校验 → 存储 → 导出 |
| ✅ 手动登录 + 会话保持 | storage_state.json |
| ✅ 页面诊断 | HTML/截图/元素统计/网络捕获/分类发现 |
| ✅ HTML 表格解析 | 通用 + 按分类区块，中文表头映射 |
| ✅ 数据清洗 | Decimal 转换、千分位、日期推导（跨年）、字符串标准化、Unicode 规范化 |
| ✅ 数据校验 | 价格逻辑、日间波动检测、必填字段、日期异常、负数检测 |
| ✅ SQLite 存储 | 业务唯一键去重，价格变化更新，locked 重试 |
| ✅ 近三日均价 | 从 SQLite 查询最近 3 个价格日期，同产品分组计算 Decimal 均价 |
| ✅ Excel 导出 | 动态分类 Sheet、中文列名、近三日均价、近三日有效天数 |
| ✅ CSV 导出 | 每日总 CSV + 每分类单独 CSV |
| ✅ 历史汇总 + 固定汇总 | 自动累积去重，三分类全部成功时更新 |
| ✅ MySQL 同步 | 自动建库建表、批量 upsert (record_hash)、3 表结构 |
| ✅ 数据质量报告 | JSON 格式，包含采集+同步统计 |
| ✅ 领导汇报报告 | 产业链全景 Excel（按上游/中游/下游/回收组织） |
| ✅ 日志系统 | 双文件（全量 + 仅错误）+ 控制台 |
| ✅ CLI 参数 | --date、--category、--headed、--dry-run |
| ✅ MySQL 同步 CLI | --date、--start-date/--end-date、--full、--dry-run、--quality-report |
| ✅ Windows 定时任务 | 每日 10:00 自动采集 |
| ✅ 测试 | **74 个测试**全部通过 |
| ✅ 重试机制 | SQLite locked 重试 + MySQL 同步 3 次重试 |
| ✅ 配置化 | settings.yaml 控制分类模式、同步、近N日窗口等 |

### 数据现状

- SQLite：**1634 条记录**，覆盖 **2025-11 至 2026-07**，40 个分类
- MySQL：三张表自动同步
- 每日 Excel：自动展示最近 3 个价格日期的近三日均价

### 未完成

| 功能 | 状态 |
|------|------|
| ❌ 历史日期补采 | backfill.py 仅桩实现 |
| ❌ 自动登录 | 需先确认表单选择器 |
| ❌ 分页翻页 | selectors.yaml 分页配置为空 |
| ⚠️ API 优先采集 | prefer_api: true 未实现 |
| ⚠️ 邮件/消息通知 | 未实现 |

### 材料覆盖缺口（待补充外部数据）

市场价格模板 39 项材料中，SMM 来源 28 项**已全部采集**（部分通过名称映射匹配）。
以下 **15 项缺失来自非 SMM 来源**，需手动提供：

| 来源 | 缺失项 | 数量 |
|------|--------|------|
| **Benchmark** | NCM黑粉（日韩/北美/欧洲） | 3 |
| **江苏华友客户反馈** | 工商业储能系统(液冷)、小动力电池6020/6030/6050 | 4 |
| **华宝** | 5系/6系/8系/9系/NCA三元正极材料 | 8 |

> 外部数据放入 `data/external/external_prices.xlsx` 即可自动填充到报表中。
> 华宝三元材料需确认数据来源后再配置。

### 已知问题

- pandas `FutureWarning`：空的 DataFrame concat 行为将变化
- 没有 Git 仓库
- `.pytest_cache` 写入权限问题（不影响测试）
