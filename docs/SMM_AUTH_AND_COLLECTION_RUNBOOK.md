# SMM 登录态与价格采集运行维护手册

> 本手册面向运维人员。日常无人值守；只在异常时按步骤操作。
> 版本：2026-09-18（V2 生产化首版）

---

## 1. 系统目标

让 SMM 锂电现货价格采集系统 **长期稳定运行**，登录态尽量不丢失。

- **数据源**：https://new-energy.smm.cn/new_energy/14042
- **认证**：Persistent Chromium Profile（每次启动复用同一 profile，Cookie 不丢失）
- **日常**：Linux 自动采集；无人值守
- **普通登录失效**：自动账号密码尝试一次
- **触发验证挑战**：Linux + TurboVNC + Chromium 人工登录一次
- **不再使用**：Windows 浏览器登录 → 导出 Cookie → 上传 Linux → 替换 Cookie 文件

---

## 2. 正常运行架构

```
Cron (09:05 / 09:30 / @reboot)
  ↓
scripts/run_daily.sh（flock 互斥）
  ↓
scripts/smm_supervisor.py
  ↓
1. Network Check（httpx 探测 SMM 可达性）
3. Open Browser（Persistent Profile / 或 legacy storage_state）
4. Auth Health Check（check_auth 综合判定）
   ├─ AUTH_OK → 继续
   ├─ AUTH_EXPIRED → 触发 Auto Login（单次）
   ├─ AUTH_VERIFICATION_REQUIRED → 停（提示人工）
   ├─ AUTH_LOGIN_FAILED → 停（提示凭据错）
   └─ AUTH_NETWORK_ERROR → 网络重试
5. main.collect()（采集 + 解析 + 校验 + 入库）
6. Post-run Verify（行数 / price_date / 登录墙二次校验）
7. Export / Validation / MySQL Sync / Notifier
8. auth_status.json + collector_status.json 原子写
9. ops_events 记录关键节点
```

---

## 3. 正常情况下我什么都不用做

### 3.1 每日运行时间

| 任务 | 时间 | 触发 |
|------|------|------|
| 主采集 | 工作日 09:05 | cron |
| 兜底补采 | 工作日 09:30 | cron |
| 开机补采 | @reboot | cron |

### 3.2 检查健康状态

```bash
cd /root/smm-lithium-collector
.venv/bin/python scripts/smm_health.py
```

正常输出（stderr 摘要）：

```
auth=AUTH_OK (reason=all checks passed)
collector=success last_success=2026-09-18T09:05:12 data_date=2026-09-17 consecutive_failures=0
staleness=ok (today already succeeded at 2026-09-18T09:05:12)
next_action: no action needed
```

退出码：
- `0` — 一切正常
- `1` — WARN（需关注，但未失败）
- `2` — CRIT（必须介入）

---

## 4. 登录状态保存在哪里

| 文件 / 目录 | 路径 | 权限 | 用途 |
|------|------|------|------|
| Browser Profile | `/var/lib/smm-collector/browser-profile` | 0700 root | Cookie + Local Storage + IndexedDB |
| Profile Lock | `/var/lock/smm-collector-browser.lock` | 0600 root | 防止多进程同时打开 profile |
| Auth Status | `/var/lib/smm-collector/auth_status.json` | 0644 root | 最近一次 auth check 结果 |
| Collector Status | `/var/lib/smm-collector/collector_status.json` | 0644 root | 最近一次采集运行结果 |
| Secrets | `/etc/smm-collector/secrets.env` | 0600 root | SMM_USERNAME / SMM_PASSWORD |
| Run Lock | `/var/lock/smm-collector-run.lock` | 0600 root | 防止 09:05 / 09:30 重叠 |
| 旧 storage_state（fallback） | `data/auth/storage_state.json` | 0600 root | V1 备份；V2 失败时回滚 |

---

## 5. 登录失效怎么办

### 5.1 自动恢复（无需人工）

普通失效（无验证挑战）：
1. 09:05 cron 触发 Supervisor
2. check_auth 返回 `AUTH_EXPIRED`
3. Supervisor 自动调用 `guarded_auto_login()`（**仅 1 次**）
4. 用户名密码从 `/etc/smm-collector/secrets.env` 读取
5. 填表 → 提交 → 等待跳转 → check_auth 复验
6. 复验 `AUTH_OK` → 继续采集；持久 profile 自动保留新 Cookie

无需任何人工操作。

### 5.2 人工恢复（验证挑战）

如果系统出现验证码 / 短信验证 / 滑块 / 设备验证 / 风险验证：
- Supervisor **不会绕过**，立即返回 `AUTH_VERIFICATION_REQUIRED`
- 采集失败，钉钉会推送告警

#### Step 1：SSH 连接服务器（Windows 端）

```powershell
ssh root@106.12.59.96
```

#### Step 2：建立 VNC 隧道

```powershell
ssh -L 5999:127.0.0.1:5999 root@106.12.59.96
```

#### Step 3：在 Windows 端打开 VNC 客户端

连接到 `localhost:5999`（TurboVNC 推荐；密码向管理员索取）。

#### Step 4：在 Linux Chromium 内登录 SMM

在 VNC 桌面打开 Chromium，访问 https://user.smm.cn/login，完成登录（含可能的验证挑战）。

#### Step 5：回到终端执行健康检查

```bash
cd /root/smm-lithium-collector
.venv/bin/python scripts/smm_auth_init.py --login
```

脚本会：
- 启动 headed Chromium（复用 VNC 显示）
- 等待用户在 Chromium 内完成登录
- 用户在终端按 Enter
- 脚本执行 `--check` 复验
- 必须看到 `AUTH_OK`

#### Step 6：登录成功后脚本询问

```
Authentication recovered (AUTH_OK).
Today's collection: status=<X>
Run catch-up now? [y/N]
```

- 输入 `y` → 触发 catchup
- 输入 `N` 或回车 → 跳过（等下一个 cron 周期）

---

## 6. 登录恢复后怎么办

### 6.1 健康检查

```bash
.venv/bin/python scripts/smm_health.py
```

确认 `auth=AUTH_OK`、`staleness=ok`。

### 6.2 今天采集未成功

如果 `collector.status=failed` 或 `last_success_at != today`：

```bash
# 触发 catchup
bash scripts/catchup_daily.sh
```

catchup 内部自动调用 `smm_supervisor.py`，重新完成采集全流程。

### 6.3 历史漏采

```bash
# 列出缺失日期
.venv/bin/python scripts/backfill_history.py --dry-run

# 人工确认后真实回填
.venv/bin/python scripts/backfill_history.py --date-start YYYY-MM-DD --date-end YYYY-MM-DD
```

**禁止**：
- 复制昨天的价格填充缺失日期
- 手工插入数据
- 跳过 validate_row / db.upsert

---

## 7. 如何判断今天有没有成功采集

```bash
.venv/bin/python scripts/smm_health.py
```

或读 `collector_status.json`：

```bash
cat /var/lib/smm-collector/collector_status.json | python3 -m json.tool
```

关注字段：
- `last_attempt_at`：最近尝试时间
- `last_success_at`：最近成功时间
- `latest_price_date`：最新价格日期（注意：可能 D-1）
- `parsed_rows` / `validated_rows`：解析/校验行数
- `inserted_rows` / `updated_rows` / `duplicate_rows`：DB 写入统计
- `consecutive_failures`：连续失败次数（≥2 → CRIT）

---

## 8. 常见异常

| 状态 | 含义 | 是否写库 | 怎么解决 |
|------|------|----------|----------|
| `AUTH_EXPIRED` | Cookie 过期 | 否 | 自动尝试一次账号密码登录 |
| `AUTH_VERIFICATION_REQUIRED` | 触发验证码/短信/滑块/风控 | 否 | **人工**：SSH + VNC + Chromium 登录一次（§5） |
| `AUTH_LOGIN_FAILED` | 账号或密码错误 | 否 | 检查 `/etc/smm-collector/secrets.env`（权限 0600） |
| `AUTH_NETWORK_ERROR` | 登录阶段网络错 | 否 | 网络重试（3 次：0s/+60s/+180s） |
| `NETWORK_ERROR` | 整体网络层失败 | 否 | 检查服务器能否访问 `https://new-energy.smm.cn` |
| `ZERO_ROWS` | 解析后 0 行（疑似登录墙） | **否** | 立即停；人工登录 SMM |
| `PARSE_FAILED` | 页面结构变化解析失败 | 否 | 截图存 `data/screenshots/`；保留原数据；人工 review |
| `VALIDATION_FAILED` | 校验通过率 < 95% | 否 | 截图存 `data/screenshots/`；保留原数据；人工 review |
| `DATABASE_FAILED` | SQLite 写入失败 | 否 | 检查磁盘空间；检查 DB 锁；保留原数据 |
| `COLLECTOR_STALE` | 计划时间后仍无成功 | 否 | 见 §9 |

---

## 9. Staleness 检测

Staleness 不只看 `price_date`（页面数据日期可能因周末/节假日延后）。

判定逻辑：
- 工作日 09:05 + 30 分钟（09:35）宽限期内 → 容忍
- 工作日 09:30 + 45 分钟（10:15）宽限期后仍无成功 → WARN
- 连续失败 ≥ 2 → CRIT

如果 `staleness=crit`：
1. `python scripts/smm_health.py` 看 `next_action`
2. 检查最近 cron 日志
3. 检查 auth_status 决定是否人工登录

---

## 10. 禁止事项

- ❌ **不要** 删除 `/var/lib/smm-collector/browser-profile`
- ❌ **不要** 两个 Chromium 同时打开同一 profile（profile_lock 防并发）
- ❌ **不要** 把 Cookie / storage_state 提交 Git
- ❌ **不要** 把 `secrets.env` 提交 Git
- ❌ **不要** 绕验证码 / OCR / 模拟滑块
- ❌ **不要** 复制昨天价格填充
- ❌ **不要** 手工修改 price_date
- ❌ **不要** 用 `collected_at` 冒充 `price_date`
- ❌ **不要** 删除 `data/auth/storage_state.json`（V1 fallback）

---

## 11. 回滚

如果 V2 出现不可恢复问题：

```bash
cd /root/smm-lithium-collector

# 1) 配置回滚（关 V2）
git checkout HEAD~N -- config/settings.yaml
# 或手动改：persistent_profile=false / auto_login_enabled=false / supervisor.enabled=false

# 2) cron 回滚
crontab logs/crontab.bak.YYYYMMDD_HHMMSS

# 3) 旧 storage_state 还原（如被覆盖）
cp data/auth/storage_state.json.bak.YYYYMMDD_HHMMSS data/auth/storage_state.json

# 4) 验证 legacy 路径
.venv/bin/python scripts/smm_auth_init.py --check

# 5) 重启服务（如需要）
systemctl restart smm-fileserver
```

回滚可在分钟内完成；旧 `browser.py` + `storage_state.json` 路径全程保留。

---

## 12. 快速命令清单

| 目的 | 命令 |
|------|------|
| 健康检查 | `python scripts/smm_health.py` |
| 人工登录 | `python scripts/smm_auth_init.py --login` |
| 只读 auth 检查 | `python scripts/smm_auth_init.py --check` |
| Supervisor dry-run | `python scripts/smm_supervisor.py --dry-run --date 2026-09-18` |
| 真实采集 | `python scripts/smm_supervisor.py --date 2026-09-18` |
| 历史回填预览 | `python scripts/backfill_history.py --dry-run` |
| 看 auth 状态 | `cat /var/lib/smm-collector/auth_status.json \| python3 -m json.tool` |
| 看 collector 状态 | `cat /var/lib/smm-collector/collector_status.json \| python3 -m json.tool` |
| 触发 catchup | `bash scripts/catchup_daily.sh` |
| 回滚（配置） | `git checkout HEAD~N -- config/settings.yaml` |
| 回滚（cron） | `crontab logs/crontab.bak.YYYYMMDD_HHMMSS` |
