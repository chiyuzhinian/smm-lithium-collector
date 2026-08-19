# 生产环境部署状态文档

> **文档日期**：2026-08-19
> **服务器公网 IP**：106.12.59.96
> **适用范围**：后续维护、服务器迁移、ICP 备案完成后的恢复

---

## 1. 项目概述

SMM 锂电现货价格每日采集系统（smm-lithium-collector）：

- 每日自动采集 SMM（上海有色网）锂电现货页面约 40 个分类的现货价格数据，覆盖锂电全产业链（上游矿 → 中游材料 → 下游电芯 → 回收）
- 数据存入 SQLite（本地）+ MySQL（同步备份），每日导出 Excel/CSV 报表
- 附带数据门户（8888 端口）：今日价格、历史数据、业务专题、数据质量面板、管理员运维中心
- 门户含账号体系（表单登录 + Session，账号库 `/var/lib/smm-fileserver/auth.db`）

**本次变更背景**：域名 `price.ldhs.online` 的 ICP 备案尚未完成，为让系统可先行对外访问，新增 Nginx 公网 IP 入口。

---

## 2. 当前服务器架构

### 2.1 SMM 价格展示系统（主要系统）

```
公网IP 106.12.59.96
        │
        ▼
   Nginx (:80)
   ip-access 站点（default_server，临时入口）
        │  proxy_pass
        ▼
   SMM 门户服务 (127.0.0.1:8888)
   systemd 服务：smm-fileserver.service
        │
        ├──▶ SQLite：本地价格数据库（/root/smm-lithium-collector/data/）
        ├──▶ MySQL：同步备份（smm_price_records 等 3 张表）
        └──▶ 门户账号库：/var/lib/smm-fileserver/auth.db
```

- 项目路径：`/root/smm-lithium-collector`
- 运行方式：systemd 服务（`smm-fileserver.service`），非特权用户 smmweb 运行

### 2.2 Global EV & Battery Policy Intelligence System（附属系统）

- 项目路径：`/opt/global-policy-intelligence`
- 部署方式：Docker Compose（`/opt/global-policy-intelligence/docker-compose.yml`）
- 访问方式：通过 Nginx 代理（与 SMM 系统共用 nginx 服务）
- 本次变更**未改动**该系统的 Docker 配置

---

## 3. 当前访问方式

### 公网 IP 访问（当前可用）

```
http://106.12.59.96
```

- 入口：Nginx `ip-access` 站点（default_server，匹配任意 Host）
- 代理目标：`127.0.0.1:8888`

### 域名访问（暂不可用）

```
http://price.ldhs.online
```

- Nginx 域名站点配置已就绪且保持启用（未修改）
- **暂不可用的原因**：ICP 备案未完成，域名无法正常对外解析/访问
- 备案完成后按「第 8 节」方案恢复

---

## 4. Nginx 配置说明

### 4.1 新增站点：`/etc/nginx/sites-available/ip-access`

- **作用**：临时公网 IP 入口（ICP 备案完成前使用）
- 已软链启用：`/etc/nginx/sites-enabled/ip-access -> /etc/nginx/sites-available/ip-access`
- 监听 `80` 端口，`default_server` + `server_name _`，匹配所有未命中域名站点的请求

```nginx
# 临时公网 IP 访问入口（ICP 备案完成前使用）
# 回滚：删除 sites-enabled/ip-access 软链接，恢复 sites-enabled/default
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8888/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300;
        proxy_send_timeout 300;
    }
}
```

### 4.2 原有站点：`/etc/nginx/sites-available/price.ldhs.online`

- **状态**：保持不变（未修改），且仍在 sites-enabled 中启用
- 监听 80 端口，`server_name price.ldhs.online`，代理到 `127.0.0.1:8888`
- 备案完成后域名请求将自动命中此站点

### 4.3 本次变更未改动的内容

- SMM 业务代码：未修改
- Docker 配置（Policy 系统）：未修改
- 数据库：未修改

---

## 5. 服务状态检查命令

```bash
# 查看 Nginx 服务状态
systemctl status nginx

# 检查 Nginx 配置语法
nginx -t

# 查看端口监听（应看到 :80 nginx 与 :8888 python）
ss -tlnp

# 查看 SMM 门户服务状态
systemctl status smm-fileserver

# 查看全部服务概览
systemctl status
```

> 2026-08-19 实测：`nginx.service` 与 `smm-fileserver.service` 均 active；80 端口由 nginx 监听，8888 端口由 python（smm-fileserver）监听。

---

## 6. Git 版本状态

| 项目 | 值 |
|------|-----|
| 当前分支 | `feature/auth-admin-dashboard` |
| 当前 HEAD | `a7633cc` |
| Commit 信息 | `backup auth admin dashboard before nginx migration` |
| 工作区状态 | clean（2026-08-19 核实） |

**规则：后续任何生产服务器修改前，必须先创建新的 git checkpoint（commit），作为回滚节点。**

---

## 7. Nginx 备份

| 备份 | 路径 | 状态 |
|------|------|------|
| 修改前备份 | `/root/nginx-backup-before-ip-access` | ✅ 存在（2026-08-19 16:25，/etc/nginx 全量副本） |
| 修改后备份 | `/root/nginx-backup-ip-access-success` | ⚠️ **尚未创建**（2026-08-19 全盘核实不存在） |

> ⚠️ 注意：「修改后备份」目录目前不存在，建议尽快补做：
>
> ```bash
> cp -a /etc/nginx /root/nginx-backup-ip-access-success
> ```

---

## 8. 备案完成后的恢复方案

ICP 备案完成后，按以下步骤移除临时 IP 入口、恢复域名访问：

```bash
# 1. 删除临时 IP 入口软链接
rm /etc/nginx/sites-enabled/ip-access

# 2. price.ldhs.online 站点保持启用，无需额外操作
#    （其配置从未修改，域名请求将自动命中）

# 3. 校验配置语法
nginx -t

# 4. 平滑重载 Nginx（不中断现有连接）
systemctl reload nginx
```

**注意事项**：

- `ip-access` 配置注释中提到的「恢复 sites-enabled/default」——当前 `default` 站点**并未启用**（sites-enabled 中无 default 软链接）。移除 ip-access 后，`price.ldhs.online` 将成为 80 端口的实际默认站点。如需恢复默认站点行为，请人工确认后再决定是否启用 `sites-enabled/default`。
- 移除 ip-access 后，公网 IP 直连访问将不再命中任何站点（80 端口仍由 price.ldhs.online 站点响应，但 Host 不匹配时将回落到该站点的默认行为，取决于 nginx 版本与配置顺序）。
- 如需保留一段过渡期，可先「禁用」而非「删除」：将软链接移出 sites-enabled 目录即可。

---

## 9. 后续开发规范

**以后修改生产服务器，必须按以下流程执行：**

1. `git status` — 确认当前工作区状态
2. `git commit` — 创建版本节点（checkpoint），作为回滚基线
3. 备份 Nginx / Docker 配置（如 `/root/nginx-backup-<变更名>-before`）
4. **Plan Mode** 确认修改方案后再动手
5. 执行修改
6. 测试验证（`nginx -t` / `systemctl status` / 页面访问检查）

> 原则：先备份、先建 checkpoint、先计划，后修改。
