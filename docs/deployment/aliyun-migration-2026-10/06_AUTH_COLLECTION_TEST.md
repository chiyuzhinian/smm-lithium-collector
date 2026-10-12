# 06 认证与采集链路测试报告

> 日期：2026-10-12 ｜ 新服务器 SMM 登录态验证与采集链路

## 1. 登录态恢复与验证

| 项 | 结果 |
|----|------|
| 恢复资产 | `/var/lib/smm-collector/browser-profile`（0700 root，备份时 2026-09-28 AUTH_OK） |
| 凭据 | `/etc/smm-collector/secrets.env`（0600 root，2026-09-18 生成） |
| 只读自检 | `smm_health.py` 恢复工作（迁移修复 sys.path 后），staleness=warn（当时今日未采集）✅ |
| **登录态实测** | **AUTH_OK**（2026-10-12 15:21，supervisor auth check 真实打开持久档案访问 SMM 判定）✅ |
| 结论 | 持久档案迁移 14 天后登录态仍有效；未触发验证墙、未做任何绕过 |

## 2. 受控采集测试（先 dry-run，经用户确认）

| 项 | 结果 |
|----|------|
| 方式 | `run_daily.sh --dry-run`（Supervisor 全链路，不写库） |
| 网络预检 | ✅ SMM 可达 |
| 认证检查 | ✅ AUTH_OK |
| 页面解析 | ✅ 447 行（39-40 分类；PACK 页面缺失 → 固定汇总 temp_snapshot，正常机制） |
| 页面数据日期 | **2026-10-12**（今日数据已发布，日期校准无需回退） |
| 日期对齐率 | 0.7405 ✅（≥0.6） |
| invalid 占比 | 0.0 ✅ |
| 每日 9 层验证 | WARNING（无 FAIL；周更品种滞后等非致命项） |
| 写库 | 0 行（dry-run 不写库）✅ |

## 3. 真实采集（用户确认后执行 ✅ 2026-10-12 15:31 完成）

| 项 | 结果 |
|----|------|
| collection_runs | **success**：40 分类成功 / 0 失败 / 447 行（target_date=2026-10-12） |
| SQLite 写入 | inserted 428 / duplicate 19 → 总量 17927 → **18358**，max price_date **2026-10-12** |
| MySQL 同步 | 先失败（迁移暴露依赖缺口）→ 修复后补同步 **新增 334 条**（data_date=2026-10-12 口径）→ 总量 17904 → **18238**，max **2026-10-12** |
| 钉钉日报 | ✅ 已推送（dingtalk ok） |
| 退出码 | 0（success） |
| 门户验证 | 公网 /api/portal/quotes 报价 price_date/collected_at = 2026-10-12 15:31 ✅ |

### 本次迁移暴露并修复的问题（已提交迁移修复分支）

| # | 问题 | 修复提交 |
|---|------|---------|
| 1 | 4 个 V2 脚本 sys.path 指向 ROOT 而非 ROOT/src → supervisor/哨兵 import 失败 | `d925259` |
| 2 | requirements.txt 从未声明 pymysql/cryptography（旧机靠手动安装）→ 每日 MySQL 同步失败 | `1963e09` |
| 3 | 新机 MySQL root 为 auth_socket 插件 → 应用 TCP 密码连接被拒（1698）；已按旧机口径改为 mysql_native_password（按 .env 密码） | 服务器侧执行 |
| 4 | 时间敏感测试 test_september_in_progress_no_mom（月份推进后失效） | `5eb55d6` |

## 4. 定时任务（用户确认后安装 ✅）

```
5 9 * * 1-5   run_daily.sh            主采集
30 9 * * 1-5  catchup_daily.sh        兜底补采
0 10 * * 1-5  smm_health_sentinel.py  健康哨兵（只读）
@reboot       catchup_daily.sh        开机补采（sleep 60）
```

09-29 至 10-11 的采集缺口：用户确认**不补采**，保持空窗。

## 5. 登录失效恢复预案（未来）

- 当前 `auto_login_enabled=false`（防 SMM 风控），登录态靠持久档案。
- 若未来 AUTH_EXPIRED：新服务器需先搭建人工登录环境（Xvfb + VNC，旧机 TurboVNC
  已随服务器销毁），再执行 `scripts/smm_auth_init.py --login`（display :99）。
- 不得绕过验证码/滑块；`smm_cred_check.py` 仅一次性诊断，不 cron、不反复运行。
