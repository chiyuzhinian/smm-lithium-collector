# 03 日报数据映射与口径（2026-10-20）

## 数据流（与网站同一口径）

```
SMM 每日全量采集 → 校验 → SQLite（data_date 校准）
        ↓
config/business_products.yaml（40 条 SMM，同一映射）
        ↓
portal_service（latest_quote / product_rows / merge_points，同一查询）
        ↓
business_daily.build_report —— 网站「每日报价」与钉钉日报共用
```

## 判定规则（实现于 business_daily.build_report）

| 状态 | 判定 |
|------|------|
| 本次有效新报价（new_quote） | 存在 price_date == data_date 且 average_price 非空的有效报价点，**且该行 collected_at >= 本次采集 started_at**（本次采集在页面看到并刷新过该行；DB upsert 对重复行刷新 collected_at） |
| 本次无新报价（no_new_quote） | 其余情况；附 price_date < data_date 的最近真实有效报价（真实日期），不伪装当日 |
| 历史暂无有效报价（no_history） | 无绑定系列（unavailable_merged）或历史无任何有效报价点；绝不制造数据 |

- 采集日期（target_date）与报价日期（price_date）严格区分：日报顶部用 target_date，数据日期不同时标注「页面数据日期 {data_date}」；每个产品行用真实 price_date。
- 单产品查询异常 → 按 no_history 处理并记 warning，不拖垮整份日报。

## 分组

- 维度：info_category（网站每日报价同款筛选维度）
- 顺序：模板行序首现（一、基础金属 → 二、金属 → 三、金属盐 → 四、废极片 → 五、黑粉 → 六、正极材料 → 七、新电芯）
- 数量在分类标题内标注，如「一、基础金属（3）」

## Excel 12 列

业务类别 ｜ 产品名称 ｜ 产品规格 ｜ 最低价 ｜ 最高价 ｜ 日均价 ｜ 涨跌 ｜ 单位 ｜ 实际报价日期 ｜ 当次报价状态 ｜ 最近有效报价 ｜ 最近有效报价日期

- 新报价行：价格四列有值、实际报价日期=该行 price_date、最近两列留空
- 无新报价行：价格四列留空、最近有效报价+真实日期如实填写、状态=「本次无新报价（附最近有效报价）」
- 无历史行：状态=「本次无新报价，历史暂无有效报价」，价格全部留空
- 40 行全量覆盖，缺失产品保留在导出结果中

## 顶部统计口径

- 采集结果 = len(success_categories)/len(expected_categories)（本次 run 的 meta，不硬编码）
- 全量数据 = meta.total_clean_rows（全量采集行数 ≠ 40 种业务产品数）
- 正文不出现 MySQL 新增/更新等技术统计（内部日志/运维监控保留）
