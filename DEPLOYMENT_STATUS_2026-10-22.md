# 部署状态存档 — 2026-10-22 域名接入（HTTPS）+ 钉钉日报格式优化

> 历史部署状态见 `DEPLOYMENT_STATUS_2026-10-20.md` 及更早存档。

## 本次变更

两项解耦需求，同一变更包：

- **钉钉日报格式**（需求 B）：标题去 SMM（正文「锂电价格采集日报」+ 卡片「锂电日报 {date}」）、静态隐藏 2 个无报价储能型产品（lfp_ess_250/240）、删除底部下载区、区块标题改「重点业务产品报价」；**修复 main.py `load_products(cfg.root)` 路径 bug**（此前生产 cron 日报一直是兜底消息、无产品明细）。
- **域名接入**（需求 A）：`price.ldhs.online` 站点启用 + 阿里云免费证书 HTTPS + http→https 301 + cookie 按协议动态 Secure + SSL 证书到期提醒（30/15/7 天钉钉）。

## 部署事实（2026-10-22）

| 项 | 值 |
|---|---|
| 生产分支 | `feature/price-domain-dingtalk-report-2026-10`（部署提交 b820da9/8753366/ef16992/5887579 + 本存档随附提交） |
| 回滚点 | `dad501a`（原生产分支 feature/dingtalk-business-report-2026-10）；文件备份 `backups/domain-dingtalk-fmt-20261022/`、nginx 备份 `backups/nginx-price-20261022/` |
| 服务器全量测试 | **545 passed 0 failed**（部署时） |
| 钉钉日报 | 标题「锂电价格采集日报」+卡片「锂电日报 {date}」；38 种产品明细（隐藏 lfp_ess_250/240）；无下载区；10-22 10:41 受控消息已发（run_id=manual-verify-2026-10-22，与 cron 防重隔离）；10-23 起 cron 自然发送 |
| 门户 | `systemctl restart smm-fileserver`（file_server.py cookie 动态 Secure）；IP 入口 HTTP 登录 cookie 无 Secure、域名 HTTPS 登录带 Secure（X-Forwarded-Proto） |
| 域名 | `http://price.ldhs.online` 301→`https://price.ldhs.online`（302→登录）；IP 入口 `http://101.37.235.13` 保持可用不受影响 |
| 证书 | 阿里云免费证书（90 天），**2026-10-22 ~ 2027-01-19**；`/etc/nginx/ssl/price.ldhs.online/{fullchain.pem 644, privkey.pem 600}`；certbot 因境外出站阻断不可用（timer 已停用，包保留） |
| 证书提醒 | cron `15 9 * * *` → `scripts/ssl_cert_expiry_check.py`（日志 logs/ssl_expiry.log）；30/15/7 天阈值钉钉提醒，按 (到期日, 阈值) 去重存 ops_events；换证后自动重新武装 |
| 存量 /dl/ | 路由保留：有效链接 200 / 篡改 404；日报不再生成新链接 |
| 零改动项 | 采集器主流程、数据库、业务映射、MySQL 同步、Excel 导出、9 层验证、cron 采集任务 |

## 待观察

- [ ] 2026-10-23（周五）9:05 cron 首份自动新格式日报（main.py 修复后 cron 首次自动带 38 产品明细）
- [ ] 用户浏览器实测：域名 HTTPS 登录 + 开发者工具确认 cookie `Secure` 标记
- [ ] 证书到期提醒首触发（预计 2026-12-20 前后，30 天档）
- [ ] 证书更换演练按 `docs/change-records/2026-10-22/price-domain-dingtalk-report/07-ssl-cert-ops-manual.md`（下次到期 2027-01-19 前执行）
