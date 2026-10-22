# 06 部署计划（2026-10-22）

前置条件（均已核实）：

- 本地测试通过并已推送 GitHub（功能分支 `feature/price-domain-dingtalk-report-2026-10`）。
- 生产分支回滚点 `dad501a`；用户明确回复「确认部署」。
- DNS 已生效；ICP 备案已通过；安全组 443 用户确认已开放（部署时实测）。

## 部署 A（钉钉日报 + cookie，代码通道 bundle）

1. 本地：`git bundle create price-domain-dingtalk-report.bundle feature/price-domain-dingtalk-report-2026-10` → `scp` 到服务器。
2. 服务器备份：`git rev-parse HEAD`（记录 dad501a）；改动文件另存 `backups/domain-dingtalk-fmt-20261022/`。
3. 服务器：`git fetch ./price-domain-dingtalk-report.bundle <branch>` + `git checkout -B <branch> FETCH_HEAD`。
4. 服务器全量测试：`.venv/bin/python -m pytest -q`（基线 541+）。
5. 重启门户（file_server.py 已改）：`systemctl restart smm-fileserver` → `/health` + IP 入口 HTTP 登录抽查（cookie 无 Secure、登录正常）。
6. 钉钉生成核对（只读脚本，不发送）：40 产品 → 38 行明细；标题 `锂电价格采集日报`；无【数据下载】；无储能型≥2.5/2.4。
7. `DINGTALK_DRY_RUN=1` 预览发送（只记日志，不真发、不写事件）。
8. 是否向正式群发一条受控消息：**届时单独征求用户确认**（用一次性 run_id，不干扰 cron 防重）；默认等次日工作日 9:05 cron 自然验证。

## 部署 B（域名 + HTTPS，nginx 层）

1. 备份站点文件与 `nginx -T` 快照（`backups/nginx-price-20261022/`）。
2. `ln -sf` 启用域名站点 → `nginx -t` → `systemctl reload nginx` → `curl -I http://price.ldhs.online/health` 200。
3. `apt install -y certbot python3-certbot-nginx`。
4. `certbot --nginx -d price.ldhs.online --redirect`（签发 + 写 443 块 + http→https 301）。
5. 443 块与 ip-access 补 `X-Forwarded-Proto $scheme;` → `nginx -t` → reload。
6. 验证：https 200 / 证书链 / 301 / certbot.timer / `certbot renew --dry-run`。
7. 回写仓库模板（实际配置 → `scripts/nginx/price.ldhs.online.conf`），如有差异随部署文档提交。

## 验收清单

域名（14 项）：

- [ ] 1 `price.ldhs.online` 解析正确（部署前已核实，复验）
- [ ] 2 域名可访问价格网站
- [ ] 3 HTTPS 证书有效、域名匹配、无证书错误
- [ ] 4 登录页面正常
- [ ] 5 登录后业务页面正常
- [ ] 6 每日报价正常加载
- [ ] 7 价格走势图正常加载
- [ ] 8 数据与报表正常加载
- [ ] 9 前后端 API 正常返回
- [ ] 10 静态资源与文件下载正常
- [ ] 11 登录会话不因域名切换异常失效
- [ ] 12 原 IP 入口未受破坏
- [ ] 13 采集定时任务与钉钉日报不受影响
- [ ] 14 其他站点/子域名不受影响

钉钉日报（12 项）：

- [ ] 1 标题为「锂电价格采集日报」，卡片标题「锂电日报 {date}」
- [ ] 2 采集结果正常展示
- [ ] 3 全量数据正常展示
- [ ] 4 不显示 MySQL 新增/更新
- [ ] 5 lfp_ess_250/240 不再展示
- [ ] 6 其余 38 种身份/分类/真实报价正确
- [ ] 7 无虚构报价
- [ ] 8 底部无数据下载区
- [ ] 9 网站 Excel/全量下载不受影响
- [ ] 10 消息生成失败有明确错误日志
- [ ] 11 重试不会产生失控重复消息（run_id 防重不变）
- [ ] 12 采集与入库测试通过

任一验收项未通过：如实报告并执行对应回滚（见 ROLLBACK.md）。
