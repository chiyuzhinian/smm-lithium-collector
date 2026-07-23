# 数据查询手册

以下所有 SQL 同时在 SQLite（本地数据库）和 MySQL（备份）中可用。

## 快速入口

```bat
REM 连接本地 SQLite（Python 一行进入交互查询）
.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/database/smm_lithium.db'); c.row_factory=sqlite3.Row; exec('while True:\\n  q=input(\"SQL> \");\\n  [print(dict(r)) for r in c.execute(q)]')"
```

用 pandas 更方便：

```bat
.venv\Scripts\python.exe
```

```python
import sqlite3, pandas as pd
con = sqlite3.connect("data/database/smm_lithium.db")

# 然后复制下面任意 SQL 执行
df = pd.read_sql("你的SQL", con)
print(df)
```

---

## 一、总体概览

```sql
-- 数据库有多少数据
SELECT COUNT(*) AS 总记录数,
       COUNT(DISTINCT category) AS 分类数,
       COUNT(DISTINCT product_name) AS 产品数,
       MIN(price_date) AS 最早日期,
       MAX(price_date) AS 最新日期
FROM lithium_spot_prices;
```

```sql
-- 每个分类有多少条记录
SELECT category, COUNT(*) AS cnt
FROM lithium_spot_prices
GROUP BY category
ORDER BY cnt DESC;
```

```sql
-- 最近采集运行历史
SELECT started_at, target_date, status, total_clean_rows
FROM collection_runs
ORDER BY started_at DESC
LIMIT 10;
```

---

## 二、按日期查询

```sql
-- 某个日期的全部数据
SELECT * FROM lithium_spot_prices WHERE price_date = '2026-07-22';

-- 最近 N 天
SELECT * FROM lithium_spot_prices WHERE price_date >= date('now', '-7 days');

-- 今天采集的数据（按采集时间）
SELECT * FROM lithium_spot_prices WHERE date(collected_at) = date('now');

-- 每天的记录数趋势
SELECT price_date, COUNT(*) AS cnt
FROM lithium_spot_prices
GROUP BY price_date
ORDER BY price_date DESC;
```

---

## 三、按分类/产品查询

```sql
-- 某个分类的全部数据
SELECT * FROM lithium_spot_prices WHERE category = '锂化合物';

-- 搜索产品名
SELECT * FROM lithium_spot_prices WHERE product_name LIKE '%碳酸锂%';

-- 某分类下有哪些产品
SELECT DISTINCT product_name, specification, unit
FROM lithium_spot_prices
WHERE category = '正极材料'
ORDER BY product_name;

-- 按产业链环节汇总（多分类组合）
SELECT category, COUNT(*) AS cnt,
       ROUND(AVG(CAST(average_price AS REAL)), 2) AS 均价
FROM lithium_spot_prices
WHERE category IN ('锂化合物', '锂矿', '锂金属', '钴金属', '钴矿')
  AND price_date = (SELECT MAX(price_date) FROM lithium_spot_prices)
GROUP BY category;
```

---

## 四、价格分析

```sql
-- 某个产品的最新价格
SELECT * FROM lithium_spot_prices
WHERE product_name = '电池级碳酸锂'
ORDER BY price_date DESC
LIMIT 5;

-- 今天哪些产品涨价了（需要两天的数据）
SELECT a.product_name, a.category,
       a.average_price AS 今日价,
       b.average_price AS 昨日价,
       ROUND((CAST(a.average_price AS REAL) - CAST(b.average_price AS REAL)), 2) AS 涨跌
FROM lithium_spot_prices a
JOIN lithium_spot_prices b
  ON a.source = b.source AND a.category = b.category
  AND a.product_name = b.product_name AND a.specification = b.specification
  AND a.unit = b.unit
WHERE a.price_date = '2026-07-22'
  AND b.price_date = '2026-07-17'
  AND a.average_price != b.average_price
ORDER BY 涨跌 DESC;

-- 各分类最高/最低/平均价格（最新日期）
SELECT category,
       MIN(CAST(average_price AS REAL)) AS 最低,
       MAX(CAST(average_price AS REAL)) AS 最高,
       ROUND(AVG(CAST(average_price AS REAL)), 2) AS 平均
FROM lithium_spot_prices
WHERE price_date = (SELECT MAX(price_date) FROM lithium_spot_prices)
GROUP BY category
ORDER BY category;
```

```sql
-- 价格异常检测（平均价不在最低价和最高价之间）
SELECT category, product_name, min_price, max_price, average_price, unit
FROM lithium_spot_prices
WHERE validation_status != 'valid'
  AND price_date = (SELECT MAX(price_date) FROM lithium_spot_prices);
```

---

## 五、产业链全景

```sql
-- 最新日期全产业链一览（按环节分组）
SELECT
  CASE
    WHEN category IN ('锂矿','锂金属','钴矿','钴金属','磷矿','镍化合物','锰化合物','铁源','碳素','电炉钢') THEN '上游·矿产'
    WHEN category = '钴化合物' THEN '上游·钴化合物'
    WHEN category = '锂化合物' THEN '中游·锂化合物'
    WHEN category = '正极材料' THEN '中游·正极'
    WHEN category IN ('人造石墨','天然石墨','天然石墨负极','新型负极','焦类') THEN '中游·负极'
    WHEN category IN ('电解液','溶剂及相关原料','添加剂') THEN '中游·电解液'
    WHEN category = '隔膜' THEN '中游·隔膜'
    WHEN category IN ('铜箔','铝箔') THEN '中游·集流体'
    WHEN category IN ('PVDF','其他辅料') THEN '中游·辅料'
    WHEN category = '磷化工' THEN '中游·磷化工'
    WHEN category IN ('电芯','储能电芯') THEN '下游·电芯'
    WHEN category IN ('电池舱','PACK') THEN '下游·电池系统'
    ELSE '回收'
  END AS 产业链环节,
  COUNT(*) AS 品种数
FROM lithium_spot_prices
WHERE price_date = (SELECT MAX(price_date) FROM lithium_spot_prices)
GROUP BY 产业链环节
ORDER BY COUNT(*) DESC;
```

---

## 六、连接 MySQL 查询

```bat
REM 用 mysql 命令行直接查
mysql -u root -p012577 smm_lithium -e "SELECT category, COUNT(*) FROM lithium_spot_prices GROUP BY category"
```

```python
# Python 连接 MySQL
import os, pandas as pd
from dotenv import load_dotenv
load_dotenv()

import mysql.connector
conn = mysql.connector.connect(
    host=os.getenv("MYSQL_HOST", "127.0.0.1"),
    user=os.getenv("MYSQL_USER", "root"),
    password=os.getenv("MYSQL_PASSWORD", ""),
    database=os.getenv("MYSQL_DATABASE", "smm_lithium"))

df = pd.read_sql("SELECT * FROM lithium_spot_prices WHERE price_date >= '2026-07-01'", conn)
print(df)
conn.close()
```

---

## 七、常用查询速查表

| 想看什么 | SQL 关键字 |
|---------|-----------|
| 今天采集了多少条 | `WHERE date(collected_at) = date('now')` |
| 某个分类的数据 | `WHERE category = '锂化合物'` |
| 搜索产品 | `WHERE product_name LIKE '%关键词%'` |
| 最新一天数据 | `WHERE price_date = (SELECT MAX(price_date) FROM lithium_spot_prices)` |
| 按分类统计 | `GROUP BY category` |
| 按日期统计 | `GROUP BY price_date` |
| 只看正常数据 | `WHERE validation_status = 'valid'` |
| 只看异常数据 | `WHERE validation_status != 'valid'` |
| 排序 | `ORDER BY average_price DESC` |
