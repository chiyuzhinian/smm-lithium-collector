# 05 — 测试结果

日期：2026-10-26/27 ｜ 环境：本地 Windows 11 + Python 3.12.10 + pytest 8.4.2（测试基线以服务器 venv 全量为准）

## 1. pytest

### 1.1 定向（本次改动相关，全部通过）

| 命令 | 结果 |
|---|---|
| `py -m pytest tests/test_web_auth.py -q` | **57 passed**（含新增 TestUserPreferences 7 例） |
| `py -m pytest tests/test_auth_gate.py::TestUserPreferencesApi -v` | **11 passed**（新增全例） |
| `py -m pytest tests/test_auth_gate.py -q` | 68 passed，**1 failed**（TestRbac::test_admin_reaches_dashboard，基线即失败，见 1.3） |

新增用例覆盖：建表幂等 / 从未保存三页全 None / 往返保序 / `[]` 与 None 区分 / UPSERT 覆盖 /
三页隔离 / ids_json 损坏降级；API 层 401 / 无 CSRF 403 / page 非法 400 / ids 非数组 400 /
往返 / 无效 id 静默丢弃 / daily_quotes 拒绝非 cp_ 格式 / **跨用户隔离** / 显式清空持久化。

### 1.2 本地全量

`py -m pytest -q --ignore=tests/test_profile_lock.py` → **549 passed, 7 failed**（582s）。

7 个失败**全部在基线 c00ab8b 的对照 worktree 中复现**（同命令同失败），确认均为既有 Windows 环境
限制、与本次改动无关：

| 失败用例 | 原因 |
|---|---|
| test_ops_monitor.py::TestDiskStatus::test_boundaries | `os.statvfs` 仅 POSIX |
| test_auth_gate.py::TestRbac::test_admin_reaches_dashboard | 同上（/api/admin/overview → ops_monitor 磁盘状态） |
| test_browser_v2.py ×4（profile_dir/ensure_profile_dir） | Unix 权限模型断言（Windows 不支持 0o700 语义） |
| test_daily_validation.py::test_pass_all_layers | 同上类环境假设（本地非测试基准，CLAUDE.md 已注明） |

服务器 venv 全量（545+18 新增 = 563 预期）待阶段 7 部署时在服务器执行确认。

### 1.3 前端静态检查

`node --check`：common.js / quotes.js / trends.js / reports.js 四个文件全部通过。

## 2. 浏览器端到端验证（Playwright + 本地演示副本）

> 方法：在系统临时目录复制项目副本（src+config+static+scripts，真实项目零接触），以最小演示库
> （电池级碳酸锂 7 个报价日 2026-10-16~26 中间缺 10-17/18/24/25 + 一条 average_price 为空的
> 磷酸铁锂（储能型）目录行）启动 file_server:8890，账号 huayou(user)/admin(admin)。
> 注：演示副本由临时文件构成，仅测试用，已与仓库隔离。

### 2.1 需求一：产品选择记忆

| 项 | 结果 |
|---|---|
| 每日报价选 2 个 catalog 产品 → 刷新恢复 | ✅ picked 与 chips 完整恢复，走 catalog 多选模式 |
| reset 后刷新不恢复旧选择 | ✅ 保存空数组（显式清空），刷新仍默认 40 品种视图 |
| 价格走势添加产品 → 重载恢复 | ✅ selected=[lce_battery] 恢复 + 图例标签渲染 |
| 走势移除产品（chip ×） | ✅ 保存空数组（API 确认 `price_trends: []`，非 null） |
| 数据与报表选产品 → 刷新恢复 | ✅ picked=[lce_industrial] + chips 恢复 |
| 退出登录 → 重登恢复 | ✅ 退出→admin→退出→huayou 后 /reports 自动恢复 lce_industrial |
| 跨用户隔离 | ✅ admin 登录后三页偏好全为 null，看不到 huayou 的选择 |
| URL `?ids=` 优先且不覆盖已存偏好 | ✅ /trends?ids=lce_industrial 展示 URL 产品，已存 [lce_battery] 不变 |
| 保存失败提示 | ✅ 模拟 API.post 失败 → toast「产品选择保存失败：模拟网络失败」，页面不崩 |
| 无效 ID 不导致页面异常 | ✅ 恢复时按当前目录过滤（代码路径 + 后端白名单测试双覆盖） |
| 未登录访问偏好接口 | ✅ pytest 401（浏览器层由会话闸门保证） |
| 换浏览器/设备 | ⚠️ 本地无法真实双设备；服务端持久化 + 退出重登/跨账号验证已覆盖等价路径（HTTP 无本地状态），待服务器验收复验 |

### 2.2 需求二：每日报价无报价过滤

| 项 | 结果 |
|---|---|
| 默认 40 品种视图隐藏无报价行 | ✅ 仅电池级碳酸锂 1 行可见，计数「共 1 个业务品种有有效报价」；LFP 储能型「暂无数据」行消失 |
| 多选模式同样过滤 | ✅ 选 2 个（1 有报价 + 1 无报价）→ 仅 1 行，计数「已选 2 · 有效报价 1 行 · 1 个无有效报价（已隐藏）」 |
| 全空时简洁空态 | ✅ 只留无报价产品 → 「所选产品在当前日期均无有效报价，请调整选择或查询日期」 |
| as_of 历史日期 | ✅ 2026-07-20（无数据）→ 全空 + 默认模式空态；2026-10-16 → 显示该日真实报价（215,000 / 10-16 / 采集 10-16 09:12），无伪造 |
| 后端不删数据 | ✅ `/api/portal/catalog/quotes` 响应仍含 quote.average_price=null 行（后端零改动） |

### 2.3 需求三：走势默认日均价 + 摘要区间均值（已确认口径）

| 项 | 结果 |
|---|---|
| 首次打开默认指标 = 日均价 | ✅ `#metric-seg button.active` = avg（JS 显式初始化） |
| 曲线用真实 average_price | ✅ ECharts series[2] 数据 = [215000, 214000, …]（与 DB 逐日一致） |
| 缺报日不造点 | ✅ 横轴仅 7 个真实报价日期（10-17/18、10-24/25 无刻度），connectNulls 断线 |
| **区间均值 = 有效点求和÷有效点数** | ✅ 7d：213,500 = 1,494,500÷7（手算一致）；custom 10-20~10-23：**213,375 = 853,500÷4**（4 个有效点，非 7 个自然日） |
| 摘要显示「日均价：xxx 元/吨」 | ✅ 「日均价：213,375 元/吨 · 区间变化 -1,000 / -0.47%（2026-10-20 → 2026-10-23）· 有效报价点 4」 |
| 最低/最高切换回归 | ✅ 最低价 = 各日 avg−500，band 口径不变；切回日均价正常 |
| 区间变化/图例/Tooltip | ✅ 区间变化按首末有效点计算正确；chips 图例与选中一致 |

### 2.4 回归

- 登录/登出/账号体系正常；月报预览与默认清单编辑未触碰（代码零改动）；
- `/api/portal/quotes` 其他消费者（today 等）不受过滤影响（过滤仅 quotes.js 内存态，后端未动）。

## 3. 未验证项（明确标注）

| 项 | 说明 |
|---|---|
| 服务器 venv 全量测试 | 待阶段 7 部署后在服务器执行（预期 563 passed） |
| 真实双设备跨设备恢复 | 本地无法搭建；已由服务端持久化等价路径覆盖，服务器验收复验 |
| 生产环境三页验收 | 待阶段 7（域名登录 + 钉钉日报 + 定时采集确认） |
| 保存失败 UI 为模拟注入 | 真实断网场景与模拟等价（API.post reject 路径一致） |
