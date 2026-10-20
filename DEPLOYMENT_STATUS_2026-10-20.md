# 部署状态存档 — 2026-10-20 钉钉业务报价日报升级

> 生产部署完成记录；历史状态见 DEPLOYMENT_STATUS_2026-08-19.md / 2026-09-*.md（旧服务器时代）。

## 本次变更

钉钉日报升级为业务报价日报：采集概况（仅采集结果+全量数据，删除 MySQL 统计）+ 40 种 SMM 业务产品按 7 个业务类别分组明细（本次无新报价产品保留位置并附最近真实报价/历史暂无标注）+ 两个 Excel 下载链接（40 产品报价 + 当日全量，公开签名免登录限时）。

## 部署事实（2026-10-20）

| 项 | 值 |
|----|----|
| 生产分支 | `feature/dingtalk-business-report-2026-10`（HEAD a927487，= 原生产 fbca17d + 4 个钉钉日报提交） |
| 部署前回滚点 | `fbca17d`；被替换 4 文件备份于服务器 `backups/dingtalk-report-pre-20261020/` |
| 服务器全量测试 | **541 passed, 0 failed**（8m47s，结果在 backups/.../pytest-full.log） |
| 门户 | smm-fileserver 已重启（/dl/ 公开签名下载路由生效），health 200 |
| 签名密钥 | `/var/lib/smm-fileserver/dl_sign.key`（0640 root:smmweb，FILE_DL_KEY 可覆盖） |
| 真实库对账 | 40 行业务日报 vs /api/portal/quotes **mismatch=0**（数据日期 2026-10-19） |
| 签名链接边界 | 有效 200；篡改签名/路径、坏格式、过期 → 404；普通路径仍 302 登录闸门 |
| DRY_RUN | 发送链路校验通过（32/40 有效新报价含采集刷新语义；不真发不写事件） |
| cron/采集/supervisor/登录态/MySQL | 零改动 |
| 变更记录 | `docs/change-records/2026-10-20/dingtalk-report/`（01~07 + ROLLBACK.md） |

## 待观察

- 下一次工作日 cron（9:05/9:30）自动采集后核对正式业务日报：顶部仅采集结果+全量数据、分类明细完整、与网站口径一致、报价日期真实、缺失状态准确、双链接可下载、无重复推送。
