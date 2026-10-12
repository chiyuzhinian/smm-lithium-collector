# 07 公网访问验收报告

> 日期：2026-10-12 ｜ 入口：http://101.37.235.13（安全组 80 已由用户放行）

## 1. 已完成的公网验收（17/17 PASS，2026-10-12 15:0x，Windows 本机经公网执行）

| # | 项 | 结果 |
|---|----|------|
| 1 | `/health` | ✅ 200 |
| 2 | 未登录访问 `/` 重定向 /login | ✅ 302 |
| 3 | 未登录 API 401 | ✅ |
| 4 | /login 页面 | ✅ 200 |
| 5 | **admin 原账号登录**（备份 auth.db 的 PBKDF2 哈希验证） | ✅ ok |
| 6 | /api/auth/me 身份 | ✅ admin/admin |
| 7 | 首页 | ✅ 200 |
| 8 | /api/overview | ✅ 200 |
| 9 | /api/portal/products | ✅ 49 行业务品种 |
| 10 | /api/portal/quotes（每日报价） | ✅ 40 条 SMM 品种 |
| 11 | /api/portal/history（走势，cell_lfp_100ah） | ✅ 有序列 |
| 12 | /api/portal/monthly（2026-09 月报） | ✅ 49 行，month_ended=True |
| 13 | 月报 Excel 下载 | ✅ 47.9 KB |
| 14 | /api/admin/overview | ✅ 200（服务端角色校验通过） |
| 15 | /admin 运维中心页面 | ✅ 200 |
| 16 | /api/key-products | ✅ 200 |
| 17 | /api/files | ✅ 200 |

历史数据验证：SQLite 17927 条（2025-11 ~ 2026-09-25）→ 门户指标/涨跌/趋势与 DB 一致（对账 5 PASS）。

## 2. 访问架构

```
公网 :80 (nginx, 安全组放行)
  ├─ server_name 101.37.235.13 → proxy_pass 127.0.0.1:8888（file_server，smmweb 低权限）
  └─ 8888 仅监听 127.0.0.1（FILE_SERVER_BIND 默认），3306 仅本地 —— 均不对公网
```

## 3. 域名 price.ldhs.online（待用户决策）

| 项 | 状态 |
|----|------|
| DNS A 记录 | 仍指向旧 IP 106.12.59.96（旧服务器已到期销毁）；改指 101.37.235.13 **需用户确认**后由用户在其 DNS 服务商处操作 |
| nginx 域名站点 | 已就位（/etc/nginx/sites-available/price.ldhs.online.conf，未启用 symlink） |
| **备案风险** | 旧备案在百度云（见 DEPLOYMENT_STATUS_2026-08-19）；**阿里云要求域名备案与服务器同主体/接入阿里云备案**，否则 80 访问域名可能被阿里云拦截。需用户核查备案状态或走阿里云接入备案 |
| 建议 | 备案完成前以公网 IP 临时入口对外（当前已可用）；域名切换 = DNS 改指 + 启用域名站点 + 备案合规 |

## 4. 待补验收（Phase 7 清单中与采集相关项）

- 采集器状态（今日真实采集结果，见 06 报告）
- 服务器重启后服务自恢复（计划验收项：重启后 smm-fileserver/nginx/mysql/cron 自动恢复——需与用户确认重启时机）
