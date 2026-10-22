# ROLLBACK 回滚方案（2026-10-22）

原则：两需求独立回滚；保留价格数据/历史报价/采集能力；不删数据库；不影响其他子域名；记录回滚原因、执行时间与结果。

## 1 钉钉日报 + cookie（代码回滚）

```bash
# 服务器（/root/smm-lithium-collector）
git checkout -B feature/dingtalk-business-report-2026-10 dad501a
systemctl restart smm-fileserver   # file_server.py 回退后重启
```

- 采集器（cron）无独立服务，次日 cron 自动用回退代码。
- 文件级兜底：`backups/domain-dingtalk-fmt-20261022/`（改动文件副本）。
- 数据零影响：本次无任何数据库/映射/采集逻辑改动。

## 2 域名/HTTPS 回滚

```bash
rm /etc/nginx/sites-enabled/price.ldhs.online        # 撤销启用
cp backups/nginx-price-20261022/price.ldhs.online.pre-certbot \
   /etc/nginx/sites-available/price.ldhs.online      # 恢复启用前内容
nginx -t && systemctl reload nginx
```

- IP 入口（ip-access default_server）全程未动，任何时刻可回退到「仅 IP 访问」状态。
- 证书不主动吊销（阿里云证书到期自动失效；如需立即失效可在控制台吊销）；私钥文件删除即可停止使用。
- certbot.timer 已停用（本次部署时 `systemctl disable --now`）；certbot 包保留未用，可 `apt remove certbot python3-certbot-nginx` 清理。
- 安全组 443 可保留（无监听即无效）或由用户关闭。

## 3 数据保障

- 本次变更不删除、不修改任何价格数据、历史报价、账号库、映射文件。
- `/dl/` 路由与签名密钥保留，存量下载链接在其 7 天有效期内不受影响。
