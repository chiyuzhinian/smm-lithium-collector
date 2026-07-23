# SMM 锂电现货价格每日采集

每天早上一条命令，自动完成：采集 → 清洗 → 校验 → SQLite 入库 → 近三日均价计算 → Excel/CSV 导出 → MySQL 同步 → 质量报告。

## 快速开始

```bat
REM 每天运行这一条即可（采集 + 自动同步 MySQL）
cd /d C:\科研\smm_lithium_collector && .venv\Scripts\python.exe scripts\run_daily.py
```

运行完成后：
- 当天 Excel：`data\exports\2026\07\23\SMM锂电现货价格_2026-07-23.xlsx`
- MySQL 数据库 `smm_lithium` 已自动同步

---

## 首次安装

```bat
cd /d C:\科研\smm_lithium_collector
py -m venv .venv
.venv\Scripts\activate
py -m pip install -r requirements.txt
py -m playwright install chromium
copy .env.example .env
```

编辑 `.env`，填写：

```
SMM_LOGIN_URL=https://user.smm.cn/login
SMM_TARGET_URL=https://new-energy.smm.cn/new_energy/14042
```

首次运行前，先登录一次：

```bat
.venv\Scripts\python.exe scripts\manual_login.py
```

浏览器打开后手工完成登录（验证码/滑块等），按 Enter 保存。

---

## 项目结构

```
smm_lithium_collector/
├── src/smm_collector/          ← 核心代码
│   ├── main.py                 ← 采集主流程
│   ├── config.py               ← 配置加载
│   ├── browser.py              ← Playwright 浏览器
│   ├── authentication.py       ← 登录态管理
│   ├── category_navigator.py   ← 分类发现与遍历
│   ├── parser.py               ← HTML 表格解析
│   ├── network_capture.py      ← XHR 网络数据捕获
│   ├── cleaner.py              ← 数据清洗
│   ├── validator.py            ← 数据校验
│   ├── database.py             ← SQLite 读写 + 多日查询
│   ├── price_statistics.py     ← 产品分组 + 近N日均价计算
│   ├── exporter.py             ← Excel/CSV 导出（近N日展示）
│   ├── mysql_database.py       ← MySQL 连接/建表/批量写入
│   ├── synchronizer.py         ← SQLite→MySQL 同步编排
│   ├── data_quality.py         ← 数据质量报告
│   └── logger.py               ← 日志
│
├── scripts/                    ← 独立脚本
│   ├── run_daily.py/.bat        ← 每日采集
│   ├── manual_login.py          ← 手动登录
│   ├── inspect_page.py          ← 页面诊断
│   ├── generate_report.py       ← 领导汇报报告
│   ├── sync_to_mysql.py         ← MySQL 同步
│   └── *_daily_task.ps1         ← Windows 定时任务
│
├── config/
│   ├── settings.yaml            ← 分类模式、输出路径
│   └── selectors.yaml           ← 页面 CSS 选择器
│
├── tests/                       ← 74 个测试
│
├── data/                        ← 运行数据（全部 gitignore）
│   ├── database/                ← SQLite 数据库
│   ├── exports/                 ← 导出 Excel/CSV
│   ├── raw/                     ← 原始 HTML/截图/JSON
│   └── screenshots/             ← 异常截图
│
├── logs/                        ← 日志文件
├── RUN_GUIDE.md                 ← 每日运行指南
├── QUERIES.md                   ← SQL 查询手册
└── CLAUDE.md                    ← 开发者文档
```

---

## 采集的 40 个分类

每次运行时自动从页面发现，覆盖锂电全产业链：

| 环节 | 分类 |
|------|------|
| 上游矿产 | 锂矿、锂金属、钴矿、钴金属、磷矿、镍化合物、锰化合物、铁源、碳素、电炉钢 |
| 上游化合物 | 钴化合物 |
| 中游材料 | 锂化合物、正极材料、人造石墨、天然石墨、天然石墨负极、新型负极、焦类 |
| 中游化工 | 电解液、溶剂及相关原料、添加剂、磷化工 |
| 中游辅材 | 隔膜、铜箔、铝箔、PVDF、其他辅料 |
| 下游制造 | 电芯、储能电芯、电池舱、PACK |
| 回收循环 | 废旧锂电池、未注液电芯/卷芯价格、废旧正极片及系数、黑粉系数/指数/价格、梯次回收价格 |

---

## 输出文件

| 文件 | 位置 |
|------|------|
| 每日 Excel（每分类一个 Sheet，含近三日均价） | `data/exports/YYYY/MM/SMM锂电现货价格_YYYY-MM-DD.xlsx` |
| 每日 CSV | `data/exports/YYYY/MM/SMM锂电现货价格_YYYY-MM-DD.csv` |
| 每分类单独 CSV | `data/exports/YYYY/MM/SMM锂电现货价格_钴金属_YYYY-MM-DD.csv` 等 40 个 |
| 历史累积汇总 | `data/exports/SMM锂电现货价格_历史汇总.xlsx` |
| 全部历史数据 | `data/exports/SMM锂电现货价格_全部历史数据.xlsx` |
| 固定汇总 | `data/exports/固定汇总/SMM锂电现货价格_固定汇总.xlsx` |
| SQLite 数据库 | `data/database/smm_lithium.db` |
| MySQL 同步库 | `smm_lithium`（三张表：smm_price_records / smm_data_quality_issues / smm_sync_runs） |

当前数据库：**1634 条记录**，覆盖 2025-11 至 2026-07，**40 个分类**。

---

## 近三日价格展示

当数据库中存在至少 3 个不同价格日期时，每日 Excel 自动展示最近 3 个实际存在数据的价格日期，并计算每个产品的**近三日均价**。

### 日期选择规则

选取 SQLite 中最近 3 个不同的 `price_date`（按自然日不连续也可），例如数据库有 `07-16`、`07-17`、`07-22`，则展示这三天。

### 产品分组

按 `source + market + category + product_name + specification + unit` 六个字段确定同一产品。

### 均价计算

```
近三日均价 = 同一产品窗口日期内 average_price 之和 ÷ 有效天数
```

- `valid` 和 `warning` 数据参与计算
- `invalid` 数据不参与
- 使用 `Decimal` 精度计算

### 缺失数据

| 有效天数 | 均价 | 说明 |
|---------|------|------|
| 3 | 三天平均 | 正常 |
| 2 | 两天平均 | 产品只有两天有数据 |
| 1 | 该日价格 | 产品只有一天有数据 |
| 0 | 空 | 三天全无有效价格 |

### Excel 新增列

| 中文列名 | 说明 |
|---------|------|
| 近三日均价 | 同一产品窗口内均价 |
| 近三日有效天数 | 实际参与计算的天数（1~3） |

同一产品在三个日期行中的**近三日均价一致**（不压缩行）。

### 数据不足三天时

正常导出已有全部日期，均价按现有天数计算，日志提示当前天数。

### 配置

`config/settings.yaml` 中 `rolling_price_export` 节：

```yaml
rolling_price_export:
  enabled: true              # 是否开启近N日展示
  window_days: 3             # 统计窗口天数
  include_warning_records: true  # warning 参与均价
  exclude_invalid_records: true  # invalid 不参与均价
  add_valid_day_count: true      # 输出有效天数列
```

### CSV 和汇总文件

| 文件 | 含三日均价 |
|------|:---:|
| 每日 Excel | ✅ |
| 每日 CSV | ✅ |
| 每分类 CSV | ✅ |
| 历史汇总 Excel | — 保持原字段 |
| 固定汇总 Excel | — 保持原字段 |

---

## 常用命令

```bat
REM === 每日采集 ===
.venv\Scripts\python.exe scripts\run_daily.py

REM === 显示浏览器窗口（排查问题） ===
.venv\Scripts\python.exe scripts\run_daily.py --headed

REM === 试运行不写数据库 ===
.venv\Scripts\python.exe scripts\run_daily.py --dry-run

REM === 只采一个分类 ===
.venv\Scripts\python.exe scripts\run_daily.py --category 锂化合物

REM === 手动登录 ===
.venv\Scripts\python.exe scripts\manual_login.py

REM === 页面诊断（网站改版后） ===
.venv\Scripts\python.exe scripts\inspect_page.py

REM === 领导汇报报告 ===
.venv\Scripts\python.exe scripts\generate_report.py

REM === 同步到 MySQL ===
.venv\Scripts\python.exe scripts\sync_to_mysql.py

REM === 运行测试 ===
.venv\Scripts\python.exe -m pytest -q
```

---

## MySQL 同步（默认自动）

采集完成后**自动同步**到本机 MySQL。在 `.env` 中配置：

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=你的密码
MYSQL_DATABASE=smm_lithium
MYSQL_AUTO_SYNC_AFTER_COLLECTION=true   # 采集后自动同步
```

MySQL 中三张表：`smm_price_records`（价格）、`smm_data_quality_issues`（异常）、`smm_sync_runs`（同步记录）。

关闭自动同步：`MYSQL_AUTO_SYNC_AFTER_COLLECTION=false`

手动同步：

```bat
.venv\Scripts\python.exe scripts\sync_to_mysql.py              # 增量同步
.venv\Scripts\python.exe scripts\sync_to_mysql.py --date 2026-07-23  # 指定日期
.venv\Scripts\python.exe scripts\sync_to_mysql.py --full       # 全量重新同步
.venv\Scripts\python.exe scripts\sync_to_mysql.py --dry-run    # 预览
```

---

## 数据字段

每行数据包含：

| 字段 | 类型 | 说明 |
|------|------|------|
| source | str | 数据来源 "SMM" |
| market | str | 市场 "SMM锂电现货" |
| category | str | 分类名（自动发现） |
| product_name | str | 品名 |
| specification | str | 规格 |
| min_price | Decimal | 最低价 |
| max_price | Decimal | 最高价 |
| average_price | Decimal | 平均价 |
| change_value | Decimal | 涨跌 |
| unit | str | 单位（元/吨、美元/千克等） |
| price_date | date | 价格日期 |
| collected_at | datetime | 采集时间 |
| validation_status | str | valid / warning / invalid |
| three_day_average_price | Decimal | 近三日均价（Excel 展示用，不存 MySQL） |
| three_day_valid_count | int | 近三日有效天数（1~3） |

---

## 运行要求

- Windows 10/11
- Python 3.11+
- Chromium（Playwright 自动下载）
- 有效的 SMM 账号
- 电脑开机 + 网络正常

## 安全说明

- 不破解验证码、不绕过登录限制
- 账号密码存在 `.env`，已加入 `.gitignore`，不提交
- 不保存 Cookie/Token 到日志
- 使用用户合法 SMM 账号采集可见数据

## 详细文档

| 文档 | 内容 |
|------|------|
| `RUN_GUIDE.md` | 每日运行指南、常见问题、定时任务 |
| `QUERIES.md` | SQL 查询手册、常用查询示例 |
| `CLAUDE.md` | 开发者技术文档、架构说明 |

---

## 部署到其他服务器

### 1. 克隆项目

```bat
git clone https://github.com/chiyuzhinian/smm-lithium-collector.git
cd smm-lithium-collector
```

### 2. 安装依赖

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 3. 配置

```bat
copy .env.example .env
```

编辑 `.env`，填写 SMM 网址、MySQL 连接、钉钉 Webhook 等。

### 4. 首次登录

```bat
.venv\Scripts\python.exe scripts\manual_login.py
```

### 5. 测试运行

```bat
.venv\Scripts\python.exe scripts\run_daily.py --dry-run

# 正常后设置定时任务
powershell -ExecutionPolicy Bypass -File .\scripts\install_daily_task.ps1
```

> 新服务器可以只配 SMM 采集，不需要 MySQL/钉钉，把 `.env` 中对应配置留空即可。

## 退出码

| 码 | 含义 |
|----|------|
| 0 | 成功 — 全部分类采集完成 |
| 2 | 部分成功 — 部分分类失败 |
| 1 | 失败 — 全部失败 |

![image-20260723100246909](C:\Users\34614\AppData\Roaming\Typora\typora-user-images\image-20260723100246909.png)
