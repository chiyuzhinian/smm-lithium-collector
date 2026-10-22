# 03 域名接入变更（2026-10-22）

目标：`price.ldhs.online` 正式接入价格门户，优先 HTTPS；原 IP 入口 `http://101.37.235.13/` 保持可用。

## 修改文件

| 文件 | 修改前 | 修改后 |
|---|---|---|
| `scripts/nginx/price.ldhs.online.conf` | 仅 :80 代理块 | :80 块 301 → https + :443 ssl 块（certbot 路径、X-Forwarded-Proto）；部署注释更新 |
| `scripts/nginx/ip-access.conf` | 无 X-Forwarded-Proto | 补 `proxy_set_header X-Forwarded-Proto $scheme;`（与旧服务器配置一致，HTTP 下行为不变） |
| `scripts/file_server.py` `_session_cookie_str` | Secure 仅受 `cookie_secure` 配置控制 | 动态：`cookie_secure=true` 或请求头 `X-Forwarded-Proto: https` 时加 `; Secure`（IP+HTTP 登录不受影响） |
| `config/categories_portal.yaml:256` | 注释「部署 HTTPS 后改为 true」 | 注释说明动态行为；`cookie_secure` 保持 false |

## Cookie 动态 Secure 设计

- 信任模型：8888 仅监听 127.0.0.1，`X-Forwarded-Proto` 只可能来自本机 nginx（两站点模板均已设 `$scheme`），无伪造面。
- 行为：域名 HTTPS 登录 → cookie 带 `; Secure`；IP HTTP 登录 → 不带。两种入口会话各自独立（host-only cookie 原有行为）。
- 配置兜底：`cookie_secure: true` 仍可强制全部 Secure（代价：IP+HTTP 登录失效），保持配置键语义。

## 生产部署步骤（收到「确认部署」后执行）

1. 备份：`cp /etc/nginx/sites-available/price.ldhs.online backups/nginx-price-20261022/pre-certbot` + `nginx -T > backups/nginx-price-20261022/nginx-T-pre.txt`
2. 启用站点：`ln -sf` → `nginx -t` → `systemctl reload nginx`；`curl -I http://price.ldhs.online/health` → 200
3. 安装：`apt install -y certbot python3-certbot-nginx`
4. 签发：`certbot --nginx -d price.ldhs.online --redirect`（HTTP-01 走 80；certbot 写入 443 块与 301 跳转）
5. 补 `X-Forwarded-Proto $scheme;` 到 443 块与 ip-access → `nginx -t` → reload
6. 验证：https 200 / 证书链与到期日 / http→https 301 / `systemctl list-timers certbot.timer` + `certbot renew --dry-run`
7. 验收（14 项，见 06）；通过后回写模板（服务器实际配置 → 仓库 `scripts/nginx/price.ldhs.online.conf`，如有差异随部署文档提交修正）

## 风险

- certbot 1.21 + nginx 1.18 标准组合；`--redirect` 只作用于域名站点，ip-access default_server 不受影响。
- 443 若实际未放行（安全组），只影响签发后的验收，不影响签发本身（HTTP-01 走 80）——发现即报告，不擅自改安全组。
- 部署模板时**不直接覆盖**现有站点文件：先启用现有 :80 配置，由 certbot 原地改写，验收通过后回写仓库模板。
