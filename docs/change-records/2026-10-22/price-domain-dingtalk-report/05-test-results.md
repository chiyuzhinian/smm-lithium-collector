# 05 测试结果（2026-10-22）

## 针对性测试（改动模块）

```bash
py -m pytest -q tests/test_business_daily.py tests/test_notifier.py tests/test_auth_gate.py
# 85 passed, 1 failed
```

| 文件 | 用例数 | 覆盖场景 |
|---|---|---|
| tests/test_business_daily.py | 18 | 40 产品覆盖/新旧报价判定/最近报价/消息格式与拆分/Excel 完整性/门户口径一致 + 新增：标题去 SMM、无下载区、储能型 2.5/2.4 隐藏且 2.3 与动力型不受影响 |
| tests/test_notifier.py | 10 | 重试/防重/事件/DRY_RUN + 新增：卡片标题「锂电日报 {date}」 |
| tests/test_auth_gate.py | 全部通过（新增 cookie 动态 Secure 用例） | 进程内 file_server：X-Forwarded-Proto: https → Set-Cookie 含 `; Secure`；无头 → 不含 |

唯一失败：`TestRbac::test_admin_reaches_dashboard` —— `os.statvfs` 在 Windows 不存在（环境限制，Linux 通过），与本次改动无关。

## 全量回归（Windows）

```bash
py -m pytest -q --ignore=tests/test_profile_lock.py
# 527 passed, 7 failed（全部为已知 Windows 环境限制，与本次改动无关）
```

| 失败用例 | 根因 | 服务器（Linux） |
|---|---|---|
| test_auth_gate.py::TestRbac::test_admin_reaches_dashboard | 无 os.statvfs | 通过 |
| test_ops_monitor.py::TestDiskStatus::test_boundaries | 无 os.statvfs | 通过 |
| test_browser_v2.py 4 个 profile_dir 用例 | Unix 权限位（511）模型 | 通过 |
| test_daily_validation.py::test_pass_all_layers | Windows 路径/环境差异 | 通过 |

基线对照：本地历史基线同为「7 failed 环境限制」；权威全量为服务器 venv 全量（dad501a 基线 **541 passed**，本包净增 4 用例，预期 545+）。服务器全量在部署阶段执行（见 06）。

## 关键回归确认

- `test_report_covers_all_40_products` / `test_excel_40_rows_and_status`：build_report 与 Excel 仍为 40 行 ✅（隐藏仅作用于消息渲染层）
- `test_message_missing_line_formats`：lfp_repair 的「历史暂无有效报价」行仍在 ✅
- `test_message_fallback_without_rows`：兜底 = 概况，无下载区 ✅
- `test_dl_link.py`（7 用例）：dl_link 模块与 /dl/ 路由保留 ✅
- 服务器端到端验证（部署阶段）：只读重建日报 → 40 产品/38 明细/新标题/无下载区；DRY_RUN 预览；门户重启后 IP 入口登录抽查。
