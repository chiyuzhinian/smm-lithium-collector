# 04 — 前端改造明细

## common.js（+20 行）

追加 `Prefs` 助手：`load(page)`（GET 偏好，ttl=0 禁用缓存，失败 notify + 返回 null）与
`save(page, ids)`（API.post，失败 reject 由调用方 toast）。三页均在 api.js/common.js 之后加载，天然可用。

## quotes.js（+37/-10 行）

| 位置 | 改动 |
|---|---|
| init（catalog 加载后、refresh 前） | 恢复：`saved=null` 不动（默认 40 品种视图）；数组 → `qstate.picked = saved.filter(id => qstate.catalogById[id])`（aliases 已并入，旧 id 自动解析）+ renderPickedChips |
| togglePicked / reset-btn | 末尾 `savePrefs()`（fire-and-forget；reset=显式清空持久化空数组） |
| refresh()（需求二核心） | `qstate.visible = qstate.rows.filter(r => r.quote && r.quote.average_price !== null && r.quote.average_price !== "")` **先过滤再 updateStatus/renderTable**（两种模式统一；顺带修复原 st-count 首屏读旧 visible 的问题） |
| updateStatus | reduce 与计数全部改用 `qstate.visible`；result-count：默认模式「共 M 个业务品种有有效报价」；多选模式「已选 K 个全量产品 · 有效报价 M 行 · H 个无有效报价（已隐藏）」（H>0 时） |
| renderTable 空态 | 多选：「所选产品在当前日期均无有效报价，请调整选择或查询日期」；默认：「当前查询日期没有有效报价的品种，请调整筛选条件」 |
| rowHtml / rowHtmlCatalog | 内部 noQuote 分支保留不动（过滤后到不了这些行，作死安全兜底，最小 diff） |

**后端零改动**：`/api/portal/quotes` 继续返回全量行（quote:null 表达无报价），today.js 等其他消费者不受影响。

## trends.js（+37/-8 行）

| 位置 | 改动 |
|---|---|
| init（bindControls 后） | 指标默认态 JS 显式初始化：`#metric-seg button` active 与 `tstate.metric`（默认 "avg"=日均价）同步，JS 为唯一事实源 |
| init（URL 解析处） | URL `?ids=` 优先（显式导航，不触发保存）；无 URL 才 `Prefs.load("price_trends")` → 按 `tstate.products` 白名单过滤 → silent addProduct |
| init（loadData 后） | **补 `renderChips()`**：恢复后渲染产品图例标签，与选中状态/图表一致（原 URL 跳转路径同样缺失） |
| suggest-item click / chips legend-remove | 加 `saveSelected()`（fire-and-forget + 失败 toast）；不在 addProduct/removeProduct 内部保存（恢复流程不写库） |
| renderSummary 单产品分支 | 「期末日均价 X（日期）」→「日均价：X 元/吨」，**X = 窗口内有效报价点 average_price 求和 ÷ 有效报价点数量**（非自然日）；精度沿用区间变化规则（|v|≥100→0 位，≥1→2 位，否则 4 位）+ 千分位；区间变化/有效报价点/多产品分支不变 |

**曲线算法零改动**：buildCardOption 仍取真实 `average_price`、category 轴只列真实报价日期、
`connectNulls:false`、最低/最高 band 口径不变。

## reports.js（+17 行）

- init（products 加载后、searchDataset 前）：恢复 `rstate.ds.picked`（业务 id 白名单过滤）+ renderProdChips（恢复不触发保存）；
- suggest-item click / chips legend-remove / ds-reset 三处加 `saveDsPicked()`（reset=显式清空）；
- 月报默认清单编辑（admin 全局机制）**不动**；不持久化 from/to。

## HTML

index/trends/reports 三个 HTML **零改动**。
