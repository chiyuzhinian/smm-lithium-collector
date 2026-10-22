# 01 需求确认（2026-10-22）

需求来源：用户任务书《生产系统变更任务：价格网站域名接入 + 钉钉日报格式优化》（2026-10-22）。

## 已确认决策（用户拍板）

| 项 | 决策 |
|---|---|
| 需求 B-1 标题 | 删除 SMM：正文「锂电价格采集日报」；钉钉卡片标题「锂电日报 {date}」 |
| 需求 B-2 隐藏产品 | 静态隐藏 `lfp_ess_250` / `lfp_ess_240`（现阶段不展示；日后如需恢复由用户自行修改代码列表） |
| 需求 B-2 区块标题 | 【40 种重点业务产品报价】→【重点业务产品报价】 |
| 需求 B-3 下载区 | 删除钉钉消息底部整个【数据下载】区；网站导出/下载功能全部保留 |
| 需求 A HTTPS | certbot（Let's Encrypt）+ 域名 http→https 301；IP 入口不跳转；443 安全组用户确认已开放 |
| 需求 A Cookie | 按请求协议动态 Secure（X-Forwarded-Proto: https 时加 Secure），不全局改 `cookie_secure=true` |

## 确认的需求复述

1. `price.ldhs.online` 接入现有价格门户，优先 HTTPS（certbot 自动续期），域名 http→https 301；原 IP 入口 `http://101.37.235.13/` 保持可用、不跳转。
2. 钉钉日报标题删除 SMM 字样（正文 + 卡片标题两处）。
3. 仅隐藏已核实的 `lfp_ess_250/240` 两个产品，不影响其他产品和网站本身（数据库/映射/Excel/门户均不动）。
4. 删除钉钉日报底部整个数据下载区，保留网站原有下载功能。
5. 保留采集结果、全量数据与现有业务报价明细。
6. 不展示 MySQL 新增、更新数量（现状已满足）。
7. Cookie 动态 Secure：两种入口登录均正常。
8. 流程：先审计（已完成）→ 独立分支本地开发+测试 → 推送 GitHub → 用户「确认部署」→ 生产部署（B 先、A 后，各自验收各自回滚）。

## 审计发现的生产现状修正（用户已知悉并纳入方案）

- **生产 10-21 正式日报实为兜底消息（无产品明细）**：`main.py:234` 传 `cfg.root`（项目根）而非 config 目录，`portal_service.load_products` 拼接 `business_products.yaml` 无回退 → 40 产品加载失败静默降级。已在生产服务器复刻验证（products=0；ops_events 无 10-21 的 DINGTALK_REPORT_BUILT）。本包修复为 `load_products(cfg.root / "config")`——这是需求 B 在生产生效的前提。
- 用户截图中的产品明细来自 2026-10-20 16:43 测试消息（`DINGTALK_TEST_MESSAGE_SENT`，4092 字符）。

## 基线

- 基线分支：`feature/dingtalk-business-report-2026-10`（`dad501a`，生产部署提交）
- 功能分支：`feature/price-domain-dingtalk-report-2026-10`
