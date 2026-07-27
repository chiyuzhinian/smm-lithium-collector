# SMM 锂电现货价格采集 — 每日运行指南

## 一、每天自动执行（无需手动）

已配置 Windows 定时任务：**周一至周五 9:00 自动运行**。电脑开机联网即可，钉钉群自动收日报。

如需手动运行：

```bat
cd /d C:\科研\smm_lithium_collector && .venv\Scripts\python.exe scripts\run_daily.py
```

### 首次使用：启动 ngrok（一次性）

```bat
cd /d C:\科研\smm_lithium_collector
ngrok.exe config add-authtoken 你的ngrok的token
start /b ngrok.exe http 8888
```

之后每天 `run_daily.bat` 会自动拉起 ngrok，无需手动操作。

打开终端（CMD 或 PowerShell），粘贴执行：

```bat
cd /d C:\科研\smm_lithium_collector && .venv\Scripts\python.exe scripts\run_daily.py
```

执行完毕后自动完成采集→解析→入库→导出全部流程。

## 二、运行结果怎么看

### 控制台输出

运行时会打印每个分类的采集进度，最后显示汇总统计：

```
自动发现 40 个分类：锂化合物、锂矿、锂金属、钴金属…
当前采集分类：锂化合物
当前采集分类：锂矿
...
导出：data\exports\2026\07\23\SMM锂电现货价格_2026-07-23.xlsx
最终状态=success，分类={'锂化合物': 23, '锂矿': 17, ...}，数据库={'inserted': 473}
```

退出码含义：

| 退出码 | 含义 |
|--------|------|
| 0 | success — 40 个分类全部成功 |
| 2 | partial_success — 部分成功（失败的会记录在日志中） |
| 1 | failed — 全部失败，需排查 |

### 数据去了哪里

```
┌─────────────────────────────────────────────────────────┐
│  SMM 网站 ──→ 采集 ──→ SQLite 数据库 ──→ Excel/CSV 导出 │
└─────────────────────────────────────────────────────────┘
```

| 存储位置 | 说明 |
|---------|------|
| `data/database/smm_lithium.db` | **SQLite 数据库**，所有历史数据永久存储，自动去重 |
| `data/exports/YYYY/MM/SMM锂电现货价格_YYYY-MM-DD.xlsx` | **每日 Excel**，每个分类一个 Sheet |
| `data/exports/YYYY/MM/SMM锂电现货价格_YYYY-MM-DD.csv` | **每日 CSV**，全部数据 |
| `data/exports/SMM锂电现货价格_历史汇总.xlsx` | **历史累积汇总**，每日自动追加去重 |
| `data/exports/固定汇总/SMM锂电现货价格_固定汇总.xlsx` | **固定汇总**，仅 40 分类全部成功时更新 |

### 查看日志

```bat
# 查看今天的采集日志
type logs\collector_2026-07-23.log

# 只查看错误
type logs\error_2026-07-23.log
```

## 三、MySQL 自动同步

每天早上采集完成后，默认**自动同步**到本机 MySQL。MySQL 中有三张表：

| 表 | 内容 |
|----|------|
| `smm_price_records` | 价格主表（唯一键=record_hash，自动去重） |
| `smm_data_quality_issues` | 数据质量问题（警告/错误的详细信息） |
| `smm_sync_runs` | 同步运行记录（每次同步的统计和状态） |

### 手动同步命令

```bat
REM 同步全部数据
.venv\Scripts\python.exe scripts\sync_to_mysql.py

REM 同步指定日期
.venv\Scripts\python.exe scripts\sync_to_mysql.py --date 2026-07-23

REM 同步日期范围
.venv\Scripts\python.exe scripts\sync_to_mysql.py --start-date 2026-07-01 --end-date 2026-07-23

REM 全量重新同步
.venv\Scripts\python.exe scripts\sync_to_mysql.py --full

REM 预览不写入
.venv\Scripts\python.exe scripts\sync_to_mysql.py --dry-run
```

### 关闭自动同步

在 `.env` 中设置：

```env
MYSQL_AUTO_SYNC_AFTER_COLLECTION=false
```

## 四、近三日价格展示

每天 Excel 自动展示最近 3 个价格日期并计算**近三日均价**。同一产品三行均价一致，含有效天数说明。

| 新增列 | 说明 |
|-------|------|
| 近三日均价 | 按 source+market+category+品名+规格+单位 分组，窗口内均价 |
| 近三日有效天数 | 1~3，说明用几天数据算出的均价 |

数据不足三天时正常导出。配置在 `config/settings.yaml` → `rolling_price_export`。

## 五、导出全部历史数据

```bat
cd /d C:\科研\smm_lithium_collector && .venv\Scripts\python.exe scripts/_export_history.py
```

（如脚本不存在，可用 `generate_report.py` 替代）

输出：`data\exports\SMM锂电现货价格_全部历史数据.xlsx`

## 六、常见问题处理

### 登录过期

如果控制台提示"登录状态失效"，运行：

```bat
cd /d C:\科研\smm_lithium_collector && .venv\Scripts\python.exe scripts\manual_login.py
```

浏览器会打开登录页，手工完成登录和验证码，然后按 Enter 保存状态。之后再重新运行采集命令。

### 页面结构变化

如果提示"页面上没有找到分类元素"，说明 SMM 网站改版了，运行诊断：

```bat
cd /d C:\科研\smm_lithium_collector && .venv\Scripts\python.exe scripts\inspect_page.py
```

诊断报告会保存到 `data/raw/inspection/` 目录，根据报告更新 `config/selectors.yaml`。

### 想查看浏览器运行过程

```bat
cd /d C:\科研\smm_lithium_collector && .venv\Scripts\python.exe scripts\run_daily.py --headed
```

### 只试运行不写数据库

```bat
cd /d C:\科研\smm_lithium_collector && .venv\Scripts\python.exe scripts\run_daily.py --dry-run
```

### 只采集某个分类

```bat
cd /d C:\科研\smm_lithium_collector && .venv\Scripts\python.exe scripts\run_daily.py --category 锂化合物
```

## 七、数据库查询示例

```python
import sqlite3, pandas as pd

con = sqlite3.connect("data/database/smm_lithium.db")

# 查看今天采集的所有数据
df = pd.read_sql("SELECT * FROM lithium_spot_prices WHERE price_date = date('now')", con)
print(df)

# 查看每个分类的记录数
print(pd.read_sql("""
    SELECT category, COUNT(*) as cnt
    FROM lithium_spot_prices
    WHERE price_date = date('now')
    GROUP BY category ORDER BY cnt DESC
""", con))

# 查看采集运行历史
print(pd.read_sql("SELECT started_at, target_date, status, total_clean_rows FROM collection_runs ORDER BY started_at DESC LIMIT 10", con))
```

## 八、生成领导汇报报告

```bat
cd /d C:\科研\smm_lithium_collector && .venv\Scripts\python.exe scripts\generate_report.py
```

生成产业链全景 Excel 报告，输出到 `data/exports/summary/`。

## 九、设置 Windows 定时任务（每天早上 10:00 自动运行）

```powershell
cd C:\科研\smm_lithium_collector
powershell -ExecutionPolicy Bypass -File .\scripts\install_daily_task.ps1
```

前提：电脑 10:00 时需保持开机且网络正常。

查询/删除定时任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\query_daily_task.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\remove_daily_task.ps1
```

## 十、采集的 40 个分类

| 产业链环节 | 分类 |
|-----------|------|
| 上游·矿产资源 | 锂矿、锂金属、钴矿、钴金属、磷矿、镍化合物、锰化合物、铁源、碳素、电炉钢 |
| 上游·钴化合物 | 钴化合物 |
| 中游·锂化合物 | 锂化合物 |
| 中游·正极材料 | 正极材料 |
| 中游·负极材料 | 人造石墨、天然石墨、天然石墨负极、新型负极、焦类 |
| 中游·电解液 | 电解液、溶剂及相关原料、添加剂 |
| 中游·隔膜 | 隔膜 |
| 中游·集流体 | 铜箔、铝箔 |
| 中游·辅料 | PVDF、其他辅料 |
| 中游·磷化工 | 磷化工 |
| 下游·电芯 | 电芯、储能电芯 |
| 下游·电池系统 | 电池舱、PACK |
| 回收·废旧电池 | 废旧锂电池、未注液电芯价格、未注液卷芯价格、废旧正极片及系数 |
| 回收·黑粉 | 废旧锂电黑粉系数指数、废旧锂电黑粉系数、废旧锂电黑粉价格 |
| 回收·梯次利用 | 梯次回收价格、SMM-五矿锂汇通废旧锂电池 |

> 分类列表由采集脚本每次运行时从网页自动发现，无需手动维护。如果 SMM 新增或删除分类，脚本会自动适配。
