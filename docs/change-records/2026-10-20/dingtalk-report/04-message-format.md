# 04 钉钉消息格式与可靠性设计（2026-10-20）

## 消息格式（markdown）

```text
## ✅ SMM 锂电价格采集日报
**日期**：{target_date}（页面数据日期 {data_date}，仅两者不同时显示）

【采集概况】

**采集结果**：{success}/{expected} 类别成功
**全量数据**：{total_clean_rows} 条
（失败类目>0 时：**失败类目**：{failed[:5] 顿号连接}）

【40 种重点业务产品报价】

目标产品：40 种 ｜ 本次有效新报价：{N} 种 ｜ 本次无新报价：{M} 种

一、基础金属（3）
- **A00铝**：最低 x ｜ 最高 y ｜ 日均 z ｜ 涨跌 ±w ｜ 元/吨 ｜ {price_date}
- **1#电解铜**：本次无新报价 ｜ 最近有效报价 78000 元/吨（2026-10-17）
…
【数据下载】

📥 [下载今日40种业务产品报价]({signed_url_1})
📥 [下载当日全量采集数据]({signed_url_2})
```

- 产品行：`- **{display_name}（{spec}，spec 空则省略）**：最低/最高/日均/涨跌（正值补 +）｜单位｜真实报价日期`
- 无新报价行：`本次无新报价 ｜ 最近有效报价 {avg} {unit}（{真实日期}）`；历史也无 → `本次无新报价，历史暂无有效报价`
- 状态图标沿用：✅ success / ⚠️ partial_success / ❌ failed / ❓ unknown——采集失败时如实显示，绝不发送「全部成功」日报
- 长度：单条 ≤ 18000 字符（钉钉 markdown 上限 20KB 留余量）；实测 40 产品约 3.9KB → 单条；超限时按业务类别拆分连续多条：首条含采集概况、末条含下载链接、每条（i/n）标注，产品零丢失零重复

## 发送可靠性

| 项 | 实现（notifier.py 传输层） |
|----|------|
| 重试 | send_dingtalk 失败重试共 3 次（5s/15s 退避），失败记 error 日志 |
| 防重 | 同一 run_id 全部发送成功后写 ops_events `DINGTALK_REPORT_SENT`；重复调用前查询跳过 |
| 部分失败 | 写 `DINGTALK_SEND_FAILED` 事件；不写 SENT 标记 → 同一 run 重试可补发（同 run 重试不会由 cron 触发：catchup 先查 collection_runs success） |
| 文案失败 | 写 `DINGTALK_REPORT_BUILD_FAILED` 事件 + error 日志，跳过发送 |
| 隔离 | 生成/发送异常绝不抛出，不影响采集与入库；dry-run 不构建不发送 |
| 验收 | `DINGTALK_DRY_RUN=1` 只写日志不真发、不写发送事件 |
| 凭据 | webhook/secret 仅环境变量，不入日志不入 Git |

## 下载链接（公开签名，用户已确认）

- 新模块 `smm_collector/dl_link.py`：HMAC-SHA256(key, f"{relpath}|{exp}")，默认 TTL 7 天
- 密钥文件 `/var/lib/smm-fileserver/dl_sign.key`（部署时生成，0640 root:smmweb；env FILE_DL_KEY 可覆盖）
- 链接形如 `{FILE_HOST}/dl/{sig}/{exp}/{2026/10/每日汇总/Excel/文件名}`（非 ASCII 段百分号编码）
- file_server 会话闸门之前处理 `/dl/`：验签 + 过期 + 穿越/黑名单/扩展名白名单三重校验；密钥缺失或验签失败一律 404
- 密钥缺失时 notifier 回退登录式链接（不崩溃）
