# 06 测试结果（2026-10-20）

## 新模块测试（本地 Windows，py 3.12）

```bash
py -m pytest tests/test_dl_link.py tests/test_business_daily.py tests/test_notifier.py -q
# 32 passed
```

| 文件 | 用例数 | 覆盖场景 |
|------|--------|----------|
| tests/test_dl_link.py | 7 | 签名往返 / 错密钥 / 篡改路径 / 过期 / URL 编码往返 / 密钥缺失 |
| tests/test_business_daily.py | 16 | A 全 40 覆盖、B 部分新报价、C 最近报价真实日期、D 无历史、E 与 portal_service 逐项一致、F 消息拆分零丢失、H Excel 40 行 12 列、I 顶部无 MySQL/新增/更新 |
| tests/test_notifier.py | 9 | G 失败重试 3 次 / errcode 重试 / 成功单次 / SENT 事件 / 同 run_id 防重 / 部分失败事件 / DRY_RUN 不发送不写事件 / 无 webhook |

## 关键回归

```bash
py -m pytest tests/test_portal_service.py tests/test_synchronizer.py tests/test_exporter.py tests/test_database.py tests/test_business_report.py -q
# 68 passed, 4 warnings
```

## 全量回归（本地 Windows，排除 fcntl 模块 test_profile_lock）

```bash
py -m pytest -q --ignore=tests/test_profile_lock.py
# 523 passed, 7 failed（10m47s）
```

7 个失败均为 **Windows 环境既有问题**，与本次改动无代码路径重叠（逐一复核）：

| 失败 | 根因 |
|------|------|
| test_ops_monitor::TestDiskStatus::test_boundaries | `os.statvfs` 不存在于 Windows |
| test_auth_gate::TestRbac::test_admin_reaches_dashboard | 同上（/api/admin/overview → ops_monitor.disk_status → os.statvfs），非 /dl/ 路由 |
| test_browser_v2 ×4 | Unix 文件权限位断言（0o700 等），Windows 语义不同 |
| test_daily_validation::test_pass_all_layers | 固定汇总与历史一致性夹具问题，与钉钉改动无关（未触碰 daily_validation/exporter） |

> 依据 CLAUDE.md 既有约定：本地 Windows 因 fcntl/Unix 权限模型不能作为全量测试基准；**服务器 venv 全量测试为准**（部署步骤含 `.venv/bin/python -m pytest -q`）。

## 本地预览（合成库，非真实数据，仅验证渲染链路）

- 40 行 7 组顺序正确（一、基础金属 → … → 七、新电芯）
- 37 新报价 + 3 无新报价（含 2 个 unavailable_merged 显示「历史暂无有效报价」）
- 单条消息 3857 字符（< 18000 阈值）；下载链接生成正确
- Excel 输出 `SMM锂电业务产品报价_2026-10-20.xlsx` 12 列 40 行

## 待服务器验收（部署后）

- 服务器 venv 全量 pytest
- 真实库只读对账：钉钉 40 行与 /api/portal/quotes 逐项一致
- DINGTALK_DRY_RUN=1 生成真实日报走查
- 签名链接 curl 验证（200/404 边界）
