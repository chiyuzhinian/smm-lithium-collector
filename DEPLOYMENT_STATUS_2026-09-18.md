# SMM Auth V2 Production Readiness Report — 2026-09-18

> 本报告基于 `feature/smm-auth-v2` 分支（领先 `origin/main` **9 个提交**）。
> **不**部署，等待用户明确"确认生产切换"。

---

## 1. 已交付：Phase C（保守自动登录）+ Phase E（Supervisor 守护）

### 1.1 文件清单

**新增源文件（9 个）：**
| 文件 | 行数 | 职责 |
|------|------|------|
| `src/smm_collector/supervisor.py` | ~430 | Supervisor 主类：网络 preflight → 认证 → 自动登录 → 采集 → 后验 → 状态 |
| `src/smm_collector/auth_status.py` | ~150 | auth_status.json 原子读写 + 状态机更新 |
| `src/smm_collector/collector_status.py` | ~180 | collector_status.json 原子读写 + 失败计数 |
| `src/smm_collector/network_check.py` | ~110 | httpx 探测 SMM 可达性，区分 net::ERR_* / NS_ERROR / Timeout |
| `src/smm_collector/post_run_verify.py` | ~180 | 行数 / price_date / collected_at / 登录墙二次校验 |
| `src/smm_collector/staleness.py` | ~210 | 基于计划时间 + 宽限期 + 连续失败的停更检测 |
| `src/smm_collector/completeness.py` | ~180 | ANCHOR_PRODUCTS + rolling baseline（不硬编码行数） |
| `scripts/smm_supervisor.py` | ~25 | Supervisor CLI 入口 |
| `scripts/smm_health.py` | ~55 | 统一健康检查（auth + collector + staleness） |

**修改文件（4 个）：**
| 文件 | 变更 |
|------|------|
| `src/smm_collector/config.py` | 加载 `/etc/smm-collector/secrets.env`（override=True） |
| `src/smm_collector/authentication.py` | 实现 `guarded_auto_login()`（单次尝试 + 验证挑战检测） |
| `src/smm_collector/main.py` | `meta["db_stats"] = stats` 暴露给 Supervisor 后验 |
| `config/settings.yaml` | 新增 `supervisor.*` 配置段（默认 `enabled: false`） |

**新增测试（7 个文件，+136 tests）：**
| 文件 | tests |
|------|-------|
| `tests/test_authentication.py` | 27 |
| `tests/test_auth_status.py` | 13 |
| `tests/test_collector_status.py` | 12 |
| `tests/test_network_check.py` | 21 |
| `tests/test_post_run_verify.py` | 17 |
| `tests/test_staleness.py` | 14 |
| `tests/test_completeness.py` | 15 |
| `tests/test_supervisor.py` | 17 |

**清理：**
- 删除 `scripts/_verify_auth_v2*.py`（3 个临时验证脚本）— 功能已被 `tests/` + `smm_auth_init.py --check` 覆盖

---

## 2. 测试结果

```text
456 passed, 4 warnings in 506.88s
```

基线 320 → 现 456（+136 全部 Phase C+E 测试）。

所有现有测试零回归。

---

## 3. 安全合规性自检

| 项 | 要求 | 实现 |
|----|------|------|
| 凭据存储位置 | `/etc/smm-collector/secrets.env` 0600 owner=root | ✅ `config.py` override=True 加载；不入 Python/YAML/JSON/Git/README/CLAUDE.md |
| 日志脱敏 | 禁止输出 username/password/cookie/token/session 完整值 | ✅ `authentication.py` 仅输出 `auto_login_attempt=1`、`status=AUTH_OK` 等元信息；`auth_status.ALLOWED_KEYS` 白名单 |
| 验证挑战处理 | 检测到立即停止，不绕过 | ✅ `guarded_auto_login` 检测 `验证码/短信验证/滑块验证/安全验证/风险检测/图形验证/扫码` → AUTH_VERIFICATION_REQUIRED |
| 单次尝试 | 不循环登录 | ✅ `max_attempts=1` + page 属性 `_auto_login_attempts_used` 防重试循环累积 |
| 失败可见 | 失败绝不静默 | ✅ `record_run(error_type, error_message)` + ops_events |
| 0 行 ≠ SUCCESS | 0 数据必失败 | ✅ `verify_row_counts` ZERO_ROWS → supervisor 失败 |
| 登录墙防护 | 均价全空被 parser 当产品价格 | ✅ `auth_health.check_price_wall_from_rows` + `post_run_verify.verify_no_price_wall` 双保险 |

---

## 4. 认证恢复路径

| 触发 | 自动响应 | 提示 |
|------|----------|------|
| 正常采集 | 无需操作 | — |
| Cookie 过期（无验证挑战） | guarded_auto_login 单次尝试 | 自动续登 |
| Cookie 过期 + 触发验证挑战 | 立即停 + AUTH_VERIFICATION_REQUIRED | 用户 Linux + TurboVNC headed 登录 |
| 密码错误 | 立即停 + AUTH_LOGIN_FAILED | 人工改 `secrets.env` |
| 网络层失败 | supervisor 3 次重试（0s/+60s/+180s） | 持续失败 → NETWORK_ERROR |
| 采集 0 行 | supervisor 写 ZERO_ROWS 失败 | 不写 SUCCESS |

---

## 5. 生产切换步骤（**待用户确认后执行**）

### 5.1 备份
```bash
cp data/auth/storage_state.json data/auth/storage_state.json.bak.$(date +%Y%m%d_%H%M%S)
cp config/settings.yaml config/settings.yaml.bak.$(date +%Y%m%d_%H%M%S)
crontab -l > logs/crontab.bak.$(date +%Y%m%d_%H%M%S)
```

### 5.2 创建凭据目录（仅 owner=root 可读）
```bash
mkdir -p /etc/smm-collector
chmod 700 /etc/smm-collector
# 由用户在交互式 shell 输入，不出现在命令历史
vi /etc/smm-collector/secrets.env
# 内容：
#   SMM_USERNAME=...
#   SMM_PASSWORD=...
chmod 600 /etc/smm-collector/secrets.env
chown root:root /etc/smm-collector/secrets.env
```

### 5.3 创建持久 profile 目录
```bash
mkdir -p /var/lib/smm-collector
chmod 700 /var/lib/smm-collector
mkdir -p /var/lib/smm-collector/browser-profile
chmod 700 /var/lib/smm-collector/browser-profile
touch /var/lock/smm-collector-browser.lock
chmod 600 /var/lock/smm-collector-browser.lock
```

### 5.4 切换 cron（保持现有 run_daily.sh / catchup_daily.sh 包装）
```bash
# scripts/run_daily.sh 内：把 .venv/bin/python scripts/run_daily.py 改为
#   .venv/bin/python scripts/smm_supervisor.py "$@"
```

### 5.5 启用 supervisor + auto_login（**仅连续两次 AUTH_OK 后**）
```yaml
# config/settings.yaml
auth:
  persistent_profile: true
  auto_login_enabled: true
  auto_login_max_attempts: 1
supervisor:
  enabled: true
```

### 5.6 第一次真实只读测试（人工观察）
```bash
.venv/bin/python scripts/smm_auth_init.py --check   # AUTH_OK 期望
.venv/bin/python scripts/smm_supervisor.py --dry-run --date 2026-09-18
.venv/bin/python scripts/smm_health.py              # 0/1/2 退出码
```

### 5.7 观察 09:05 / 09:30 生产 cron 第一次运行
```bash
journalctl -u smm-collector-supervisor -n 100  # 若以 systemd 运行
tail -100 logs/collector_2026-09-18.log
cat /var/lib/smm-collector/auth_status.json
cat /var/lib/smm-collector/collector_status.json
```

---

## 6. 回滚步骤（**分钟级**）

```bash
# 1) 配置回滚
git checkout HEAD~1 -- config/settings.yaml
# 或手动改：auth.persistent_profile=false / auto_login_enabled=false / supervisor.enabled=false

# 2) cron 回滚
crontab logs/crontab.bak.YYYYMMDD_HHMMSS

# 3) 旧 storage_state 还原（如被覆盖）
cp data/auth/storage_state.json.bak.YYYYMMDD_HHMMSS data/auth/storage_state.json

# 4) 验证 legacy 路径
.venv/bin/python scripts/smm_auth_init.py --check
```

旧 `browser.py` + `storage_state.json` 路径全程保留，回滚可在分钟内完成。

---

## 7. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 自动登录触发 SMM 风控 → 账号被锁 | 单次尝试；遇验证立即停止；persistent profile 减少登录频率 |
| Persistent profile 损坏 | profile_lock 防止并发；保留 storage_state 可回滚 |
| Supervisor 引入新 bug | supervisor.enabled=false 时不生效；保持 legacy 路径；dry-run 验证 |
| `/etc/smm-collector/secrets.env` 权限不当 | 0600 owner root；config.py 加载后立即丢弃 |
| 0 行误判 → 持续失败 | rolling baseline 仅 warning；0 行仍为 failure（数据为空不可接受） |
| Cron 时间漂移 | 09:05 + 09:30 宽限期 30/45 分钟，避免边界误判 |

---

## 8. Git commits（待提交）

当前 working tree 含 Phase C+E 全部代码，**未 commit**。建议 commit 顺序：

```bash
git add src/smm_collector/authentication.py
git commit -m "feat(auth): Phase C1 guarded_auto_login (single attempt + verification detection)"

git add src/smm_collector/config.py
git commit -m "feat(config): load /etc/smm-collector/secrets.env (override)"

git add src/smm_collector/{auth_status,collector_status,network_check,post_run_verify,staleness,completeness}.py
git commit -m "feat(supervisor): Phase E1 status persistence + network + verify + staleness"

git add src/smm_collector/supervisor.py scripts/smm_supervisor.py scripts/smm_health.py
git commit -m "feat(supervisor): Phase E1 Supervisor orchestrator + CLI"

git add config/settings.yaml
git commit -m "feat(supervisor): Phase E1 supervisor.* config (default enabled=false)"

git add tests/test_authentication.py tests/test_auth_status.py tests/test_collector_status.py \
        tests/test_network_check.py tests/test_post_run_verify.py tests/test_staleness.py \
        tests/test_completeness.py tests/test_supervisor.py
git commit -m "test(supervisor): Phase C+E tests (27+13+12+21+17+14+15+17=136 new tests)"
```

---

## 9. 现状总结

- ✅ 代码完成（Phase C + E）
- ✅ 安全合规（凭据/日志/验证挑战/单次尝试/0 行失败）
- ✅ 测试通过（456 passed，零回归）
- ⏸ **不部署**：等待用户明确"确认生产切换"
- ⏸ **不启用**：`persistent_profile=false` / `auto_login_enabled=false` / `supervisor.enabled=false`（默认值）
- ⏸ **不修改**：`/etc/smm-collector/` / `/var/lib/smm-collector/` / 生产 cron / Nginx / VNC

---

## 计划已完成 → 等待「确认生产切换」
