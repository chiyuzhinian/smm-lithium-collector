# 02 现状审计（2026-10-22）

只读审计：本地代码全量审查 + 生产服务器 SSH 只读探测 + 公网 DNS/HTTP 探测。审计期间未修改任何代码、配置或生产环境。

## Git 基线

| 项 | 值 |
|---|---|
| 本地/生产分支 | `feature/dingtalk-business-report-2026-10`，HEAD `dad501a`（本地=生产，一致） |
| 工作区 | 干净；仅未跟踪产物 `dingtalk-report.bundle`（上次部署 bundle，不提交） |
| 服务器 | 未跟踪 `backups/`（含 10-20 回滚备份）；cron 5 条任务正常 |

## 钉钉日报链路现状

- 数据层 `src/smm_collector/business_daily.py`（354 行）：`build_report`（40 产品行）+ `render_dingtalk_message`（标题/概况/分组明细/下载区）+ `export_quotes_xlsx`（门户每日 Excel，独立于消息）。
- 传输层 `src/smm_collector/notifier.py`：webhook 加签 + 3 次重试 + run_id 防重 + DRY_RUN；卡片标题 `SMM锂电日报 {td}`（:86）。
- 编排 `src/smm_collector/main.py:226-252`：采集尾部构建并发送，失败不影响采集。
- **Bug（已实证）**：`main.py:234` `load_products(cfg.root)` → `portal_service.load_products`（:51-58，无回退）找不到根目录下的 `business_products.yaml`（实际在 `config/`）→ products=0 → 生产日报走兜底（仅概况+下载区）。ops_events 无 10-21 的 `DINGTALK_REPORT_BUILT` 事件佐证。
- 两目标产品：`lfp_ess_250`（template_row 26）/ `lfp_ess_240`（27），`mapping_status=unavailable_merged`、`series: []` → 恒「本次无新报价，历史暂无有效报价」；实测生产库 40 产品中仅此两者命中该文案。display_name 与动力型同名——必须按稳定 ID 过滤。
- 测试：`tests/test_business_daily.py`（16 用例）、`test_notifier.py`（9）、`test_dl_link.py`（7）、`test_auth_gate.py`（file_server 端到端）。

## 域名/服务器现状

| 项 | 实测 |
|---|---|
| DNS | `price.ldhs.online` → 101.37.235.13（本机 DNS 与 8.8.8.8 均生效） |
| 域名 :80 | 已可访问门户（经 ip-access default_server → 127.0.0.1:8888），`/` 302 → `/login?next=/` |
| 域名 :443 | 连接被拒——服务器无 443 监听（nginx 无 ssl 配置）；用户确认安全组 443 已开放 |
| nginx | 1.18.0；sites-enabled 仅 `ip-access`（default_server）；`/etc/nginx/sites-available/price.ldhs.online` 已就位未启用（与仓库模板一致） |
| certbot | 未安装；apt 候选 certbot 1.21.0-1build1 + python3-certbot-nginx 1.21.0-1 |
| 本地防火墙 | ufw inactive、iptables 空 |
| 门户 | systemd `smm-fileserver` active（smmweb，沙箱），仅 127.0.0.1:8888，/health OK |
| 代码透明性 | 前端无硬编码 IP/绝对 URL；后端无 Host/Origin/Referer 校验；跳转全部相对路径 |
| Cookie | `smm_session`：HttpOnly + SameSite=Lax + Path=/，无 Domain（host-only）；Secure 仅当 `auth.cookie_secure=true`（当前 false，`config/categories_portal.yaml:256`） |

## 外部约束

- 钉钉 markdown 上限 20KB（代码余量 18000 字符，按类别块拆分）。
- 阿里云：域名需 ICP 备案（用户确认已通过）；443 安全组放行（用户确认已开放，部署时实测）。
- 服务器直连 GitHub 超时 → 代码更新走「本地 commit+push → git bundle → scp → 服务器 git pull bundle」。
- Windows 本地测试非权威基准：全量需 `--ignore=tests/test_profile_lock.py`（无 fcntl）；`TestRbac::test_admin_reaches_dashboard` 因无 `os.statvfs` 失败（Linux 通过）。权威全量为服务器 venv 全量。
