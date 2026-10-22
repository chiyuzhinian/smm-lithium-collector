# 07 SSL 证书更换与回滚操作手册（2026-10-22）

适用对象：`price.ldhs.online` 的阿里云免费证书（**90 天有效期，无自动续签，必须手动更换**）。

## 1. 当前证书与提醒机制

| 项 | 值 |
|---|---|
| 当前证书 | CN=price.ldhs.online（含 www），有效期 **2026-10-22 ~ 2027-01-19**（90 天） |
| 部署位置 | `/etc/nginx/ssl/price.ldhs.online/fullchain.pem`（644）/ `privkey.pem`（600，root） |
| 到期提醒 | cron `15 9 * * *` 跑 `scripts/ssl_cert_expiry_check.py`（日志 `logs/ssl_expiry.log`） |
| 提醒阈值 | 剩余 **30 / 15 / 7 天**时经钉钉机器人发提醒；同一到期日每阈值只提醒一次（去重存 auth.db `ops_events`，事件类型 `SSL_CERT_EXPIRY_WARN`） |
| 自动重新武装 | 部署新证书后（到期日变化），全部阈值提醒自动恢复 |
| 下次更换窗口 | 建议 **2026-12-20 前后**（到期前 30 天）发起，最迟 2027-01-12 完成 |

> 注意：提醒是「手动更换的提醒」，不是自动续期。收到提醒后必须按本手册第 2 节操作。

## 2. 证书更换流程

### 2.1 阿里云申请新证书（与首次相同）

1. 阿里云控制台 → 数字证书管理服务（SSL 证书）→ 个人测试证书（免费版）→ 创建证书
2. 绑定域名 `price.ldhs.online`，验证方式 **手动 DNS 验证**
3. 记录阿里云显示的验证记录（TXT，主机记录通常 `_dnsauth`）

### 2.2 火山引擎添加验证记录

- TrafficRoute → 域名解析 → `ldhs.online` → 添加 TXT 记录
- 主机记录 = 阿里云主机记录 + `.price`（完整域名 `_dnsauth.price.ldhs.online`）
- ⚠️ 只新增验证记录，**不改动 `price → 101.37.235.13` 的 A 记录**
- 本地验证：`nslookup -q=TXT _dnsauth.price.ldhs.online` 生效后回阿里云点「验证」

### 2.3 下载并上传

1. 状态「已签发」→ 下载 → 选 **Nginx** 格式（`.pem` + `.key`）
2. Windows 本机上传（私钥不经第三方）：
   ```bash
   scp 新证书.pem 新证书.key root@101.37.235.13:/root/smm-lithium-collector/backups/certs-新日期/
   ```

### 2.4 服务器部署（先备份再替换）

```bash
cd /root/smm-lithium-collector
# 1) 备份旧证书（回滚用）
cp -a /etc/nginx/ssl/price.ldhs.online backups/certs-旧日期-old/
# 2) 校验新证书（域名/有效期/私钥匹配）
openssl x509 -in backups/certs-新日期/*.pem -noout -subject -dates
openssl x509 -in backups/certs-新日期/*.pem -noout -modulus | md5sum
openssl rsa  -in backups/certs-新日期/*.key -noout -modulus | md5sum   # 两个 md5 必须一致
# 3) 替换并设权限
cp backups/certs-新日期/*.pem /etc/nginx/ssl/price.ldhs.online/fullchain.pem
cp backups/certs-新日期/*.key /etc/nginx/ssl/price.ldhs.online/privkey.pem
chmod 644 /etc/nginx/ssl/price.ldhs.online/fullchain.pem
chmod 600 /etc/nginx/ssl/price.ldhs.online/privkey.pem
# 4) 校验并平滑重载（IP 入口不受影响，无需改站点配置）
nginx -t && systemctl reload nginx
# 5) 公网验证
curl -sI https://price.ldhs.online/health          # 期望 200
echo | openssl s_client -connect price.ldhs.online:443 -servername price.ldhs.online 2>/dev/null | openssl x509 -noout -dates
# 6) 确认提醒重新武装
/root/smm-lithium-collector/.venv/bin/python /root/smm-lithium-collector/scripts/ssl_cert_expiry_check.py
#    （新到期日剩余 >30 天时输出「未到提醒阈值」即正常）
```

## 3. 回滚方案

```bash
cd /root/smm-lithium-collector
cp -a backups/certs-旧日期-old/price.ldhs.online/fullchain.pem /etc/nginx/ssl/price.ldhs.online/fullchain.pem
cp -a backups/certs-旧日期-old/price.ldhs.online/privkey.pem  /etc/nginx/ssl/price.ldhs.online/privkey.pem
chmod 600 /etc/nginx/ssl/price.ldhs.online/privkey.pem
nginx -t && systemctl reload nginx
```

- 回滚只需恢复证书文件并 reload；站点配置（443/301）与 IP 入口均不涉及。
- 旧证书在到期前仍有效（证书链完整即可），到期后回滚无意义——先换新。

## 4. 注意事项

- 私钥文件权限必须 600（root），**绝不入 Git、不发聊天、不输出内容**。
- 免费证书额度每自然年 20 张（含首次），3 个月一换全年只需 4 张，额度充足。
- 若希望免于季度更换，可购买一年期 DV 证书（流程相同，到期日不同，提醒机制自动适配）。
- 证书到期后（未更换）：HTTPS 出现证书错误，HTTP 域名入口经 301 跳转也会受影响；
  应急手段（仅当更换受阻）：把 `/etc/nginx/sites-available/price.ldhs.online` 的 :80 块
  301 临时改回 `proxy_pass http://127.0.0.1:8888;` 并 reload，域名恢复 HTTP 访问，
  IP 入口不受影响。
