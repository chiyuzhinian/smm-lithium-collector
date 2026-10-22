# 03 域名接入变更（2026-10-22）

目标：`price.ldhs.online` 正式接入价格门户，优先 HTTPS；原 IP 入口 `http://101.37.235.13/` 保持可用。

## 修改文件

| 文件 | 修改前 | 修改后 |
|---|---|---|
| `scripts/nginx/price.ldhs.online.conf` | 仅 :80 代理块 | :80 块 301 → https + :443 ssl 块（阿里云证书路径 `/etc/nginx/ssl/price.ldhs.online/`、TLS1.2/1.3、X-Forwarded-Proto）；部署注释更新 |
| `scripts/nginx/ip-access.conf` | 无 X-Forwarded-Proto | 补 `proxy_set_header X-Forwarded-Proto $scheme;`（与旧服务器配置一致，HTTP 下行为不变） |
| `scripts/file_server.py` `_session_cookie_str` | Secure 仅受 `cookie_secure` 配置控制 | 动态：`cookie_secure=true` 或请求头 `X-Forwarded-Proto: https` 时加 `; Secure`（IP+HTTP 登录不受影响） |
| `config/categories_portal.yaml:256` | 注释「部署 HTTPS 后改为 true」 | 注释说明动态行为；`cookie_secure` 保持 false |

## Cookie 动态 Secure 设计

- 信任模型：8888 仅监听 127.0.0.1，`X-Forwarded-Proto` 只可能来自本机 nginx（两站点模板均已设 `$scheme`），无伪造面。
- 行为：域名 HTTPS 登录 → cookie 带 `; Secure`；IP HTTP 登录 → 不带。两种入口会话各自独立（host-only cookie 原有行为）。
- 配置兜底：`cookie_secure: true` 仍可强制全部 Secure（代价：IP+HTTP 登录失效），保持配置键语义。

## 证书方案变更：certbot → 阿里云免费证书

**certbot 实测不可行**（2026-10-22 部署现场诊断）：

| 检查项 | 结果 |
|---|---|
| `acme-v02.api.letsencrypt.org` DNS 解析 | ✅ 正常（Cloudflare IP） |
| TCP 443/80 到 Let's Encrypt | ❌ 超时（`Network is unreachable`） |
| 出站访问境外站点（google.com） | ❌ 超时 |
| 出站访问国内（mirrors.aliyun.com） | ✅ 正常 |

根因：阿里云 ECS 出站访问境外站点被阻断，certbot 无法访问 LE API；且 HTTP-01 还需境外验证服务器回连 80 端口，不可靠。**用户决策：改用阿里云免费证书**（每年 20 张额度，DV 单域名，手动年续）。

## 生产部署步骤

已执行（2026-10-22）：

1. ✅ 备份：`backups/nginx-price-20261022/{price.ldhs.online.pre-certbot, ip-access.pre, nginx-T-pre.txt}`
2. ✅ 启用域名站点：symlink + `nginx -t` + reload；`http://price.ldhs.online/health` → 200
3. ✅ 停用 certbot.timer（`systemctl disable --now certbot.timer`，避免每日失败噪音）；建目录 `/etc/nginx/ssl/price.ldhs.online/`（0700）与 `backups/certs-20261022/`

待执行（证书到位后）：

4. 证书文件就位：用户上传 `.pem`/`.key` 至 `backups/certs-20261022/` → 移至 `/etc/nginx/ssl/price.ldhs.online/{fullchain.pem,privkey.pem}`（privkey chmod 600）
5. 应用站点配置：sites-available/price.ldhs.online 换为模板终态（:80 301 + :443 ssl + X-Forwarded-Proto）；ip-access 补 `X-Forwarded-Proto $scheme;` → `nginx -t` → reload
6. 验证：`curl -I https://price.ldhs.online/health` 200、证书链与到期日、`http→https` 301、IP 入口完好
7. 14 项验收（见 06）；通过后写 `DEPLOYMENT_STATUS_2026-10-22.md`
8. 每年手动续期：控制台重新申请 → 覆盖 `/etc/nginx/ssl/price.ldhs.online/` 同名文件 → `nginx -s reload`（步骤写入部署存档）

## 风险

- 阿里云证书为**手动年续**：到期前 30 天需在控制台重新申请并覆盖文件；续期步骤已写入部署存档，另在 CLAUDE.md 记录到期提醒。
- 私钥仅存服务器（0600），不入 Git、不进日志。
- `--redirect`（301）只作用于域名站点，ip-access default_server 不受影响。
- 443 若实际未放行（安全组），签发后验收会暴露——用户已确认开放，部署时实测。
