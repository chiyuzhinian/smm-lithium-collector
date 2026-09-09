# 部署状态 — 门户重构（2026-09-06）

> 本文档记录「业务品种映射 + 每日报价 / 价格走势 / 数据与报表 + 月度业务报表」重构的
> 交付状态。**2026-09-06 19:58 已部署生产 ✅**（8888 = 新版门户）。8899 预览实例
> 按用户要求保留运行，新前端完整副本仍在 `/root/smm-portal-v2-static/`。
>
> **V3 视觉专项重构（深色行情工作台）2026-09-06 当晚已部署生产 ✅**（见 §七）。

## 〇、当前状态（2026-09-06 19:58 已部署生产）

- 部署动作：新前端拷入 `static/`（含新三栏目 trends/reports）→ 品牌文案恢复
  （`portal.title` → 华友锂电行情数据中心）→ `systemctl restart smm-fileserver`
- 验证 ✓：`/health` ok；新进程正常启动，账号库 `/var/lib/smm-fileserver/auth.db`
  不变（8888 账号沿用）；9 个关键静态文件与 v2 副本逐字节一致；
  `/api/portal/products|quotes|history|monthly|monthly/download|dataset/*` 均 401
  （登录闸门生效、路由存在）；各页面正常响应（未登录 302 → /login）
- 工作区 `scripts/file_server.py` 含新后端代码（兼容旧前端所有 API）；
  `config/business_products.yaml`、`src/smm_collector/portal_service.py`、
  `monthly_report.py`、`tests/test_portal_service.py`、`docs/` 均在工作区
- 月报：`data/exports/月度业务报表/华友月度业务报表_2026-08.xlsx` 已存在（开发期生成）
- 8899 预览实例：**保留运行**（按用户要求；dev 账号 huayou/DevPass2026、
  admin/DevPass2026，auth.db=/tmp/smm-dev-auth.db，与新前端副本 `/root/smm-portal-v2-static/` 绑定）
- 备注：部署过程见下方 §〇·一，步骤已全部执行完毕

## 〇·一、部署步骤（2026-09-06 已执行 ✅）

```bash
cd /root/smm-lithium-collector
# 1. 恢复新前端（覆盖 static/ 中被回滚的旧页面）
cp -r /root/smm-portal-v2-static/* static/
# 2. 恢复品牌文案（重构版标题）
#    config/categories_portal.yaml portal.title/subtitle → 华友锂电行情数据中心
#    （回滚时已还原为旧文案；部署时改回，或从本文件 §一 的映射口径保持一致）
# 3. 重启门户
systemctl restart smm-fileserver
# 4. 验证
curl -s http://127.0.0.1:8888/health
#   登录后检查首页/走势/报表三栏目与月度报表下载
# 5. 月报 CLI（管理员离线生成）
.venv/bin/python scripts/build_monthly_report.py --month 2026-08
```


## 一、交付内容

| 层 | 文件 | 说明 |
|----|------|------|
| 映射配置 | `config/business_products.yaml` | 49 行业务品种（40 SMM + 9 非 SMM）：稳定 ID、组织/属性/类别/化学类型、业务名称与规格（含模板原文 template_detail）、精确三元组绑定（改名对/重复分类）、别名关键词、映射状态与依据。首页/走势/月报共用 |
| 数据服务层 | `src/smm_collector/portal_service.py` | as_of 最新报价（不返回未来报价）、跨分类去重（同报价点重复采集不增权重）、改名对合并、月均价+完整性（日频≥80%、周频≥75%）+环比（仅相邻两月均完整且上月非零）、全量数据分页 |
| 月报生成 | `src/smm_collector/monthly_report.py` + `scripts/build_monthly_report.py` | 49 行主表（组织合并/预测合并/列宽版式保留、动态月份标题）+ 报价明细 + 计算说明三工作表；内存生成（ProtectSystem=strict 只读可运行）；CLI 输出到 `data/exports/月度业务报表/` |
| API | `scripts/file_server.py` | 新增 `/api/portal/products|quotes|history|monthly|monthly/download|dataset/*`（登录闸门内，管理员接口权限不变） |
| 前端 | `static/index.html`(每日报价) `trends.html` `reports.html` + `css/app.css` + `js/common/quotes/trends/reports.js` + `nav.js` 重写 + login/register/account/403 重设计 | 浅色专业行情工作台：紧凑品牌顶栏+主导航、数据密集表格、等宽数字右对齐、涨红跌绿带正负号、可展开详情、ECharts 区间带/对比（调色板经 dataviz 验证） |
| 证据 | `docs/smm_lfp_price_point_change_2026.md` + 2 个公告 HTML 存档 | LFP 六行映射结论依据 |

## 二、映射结论（40 条 SMM 业务行）

- **34 条明确对应**（名称+规格+单位一致，逐行核对）；其中 3 条跨分类重复收录按报价点去重。
- **4 条官方改名延续**（动力型≥2.55/2.50/2.40、储能型≥2.30）：SMM 2026-07-17 正式公告
  （征询函 2026-03-16）改名并改定义，新定义**不含循环寿命承诺**——门户与月报标注新口径。
- **2 条无独立报价**（储能型≥2.50/2.40）：官方并入对应密度价格点（调整5/6），
  保留独立业务行与用途，显示「暂无独立报价/口径说明」，月报留空并解释原因，绝不复制同价。
- **9 条非 SMM**（Benchmark 3 / 江苏华友客户反馈 4 / 富宝 2）：保留原行与版式，价格留空人工填写。
- 模板 A00铝/1#电解铜两行「化学类型」铜铝写反：新输出按详细内容纠正，原始文件未改动。

## 三、月均价与环比口径（portal_service 实现，门户与 Excel 一致）

- 日均价 = 某一报价日期 average_price 源字段；月均价 = 当月有效报价 average_price 算术平均（按有效发布日计；周频按当月发布报价点计；同报价点重复采集不增权重；不补齐、不前向填充；排除 invalid 与无法解析值，不机械排除 warning）。
- 完整性：日频 = 当月报价日/采集日历 ≥80% 且采集日历自身覆盖当月工作日 ≥80%；周频 = 当月报价点/周五数 ≥75%。未结束月份 = 期间预览并注明。
- 环比 =（本月−上月）÷上月×100%，仅相邻两月均完整且上月非零时计算；留空时必须带原因（K 列备注 + 计算说明页）。
- 7 月采集 8/23 个工作日（<80%）→ **8 月全部环比留空并说明**（已确认业务约定）。

## 四、验证结果

- pytest **249 passed**（新增 23 个：映射/去重/改名对/四价同源/指数隔离/环比公式/Excel 49 行等）
- 15 项需求验证全部通过（真实数据库复核：同名不同规格与容量不串价、指数不混入、8 月环比 0 计算 38 留空说明、月均价可由明细复算、无公式与假性-100%、非 SMM 留空）
- Playwright 实测：桌面 1440px 与手机 390px 三栏目 0px 横向溢出、导航高亮、单位不同对比被拒、普通用户访问 /quality 与 /api/admin/* → 403、图表 canvas 像素抽样确认渲染

## 五、预览方式（2026-09-06 已部署生产；8899 预览保留）

- 生产 8888：2026-09-06 19:58 起为新版门户（部署步骤见 §〇·一）。
- 开发实例：`8899` 端口（0.0.0.0），与生产 8888 并行运行、互不影响（按用户要求保留）。
  登录：`huayou / DevPass2026`（普通用户）、`admin / DevPass2026`（管理员）。
  - 启动命令：`FILE_SERVER_PORT=8899 FILE_SERVER_AUTH_DB=/tmp/smm-dev-auth.db .venv/bin/python scripts/file_server.py`
- 月报 CLI：`.venv/bin/python scripts/build_monthly_report.py --month 2026-08`
- 示例月报已生成：`data/exports/月度业务报表/华友月度业务报表_2026-08.xlsx`

## 六、已知边界

- 6 条 LFP 业务行中 4 条按官方改名延续绑定（新口径展示并标注），2 条并入规格无独立报价（留空）。
  若业务需要"官方合并前"的历史旧口径数值，需另行确认旧口径历史来源（当前库无动力/储能独立密度系列）。
- 旧 `business_report_mapping.yaml`（规范日报）与新映射不一致处（硫酸镍/硫酸钴指数、华宝 vs 彭博团队）
  以新映射为准；旧规范日报脚本未改动、不在本次范围。
- 门户旧页面 `/today /history /topics` 保留可访问（不在主导航），未做视觉统一。

## 七、V3 视觉专项重构（深色行情工作台，2026-09-06 当晚已部署 ✅）

- **定位**：墨蓝石墨底（#0D1422/#0B1220/#151F30/#1B283D/#2A3A50）+ 克制蓝强调 #7DA7FF + 涨红跌绿；专业行情终端，非换肤。
- **规范文档**：`docs/portal-visual-spec-v3.md`（UI/UX Pro Max 三层令牌 + dataviz 六项校验）；
  技能已安装于 `.claude/skills/`（ui-ux-pro-max-cli，npx init）。
- **改动范围**：仅静态前端（index/trends/reports 三页 HTML + app.css + common/quotes/trends/reports.js +
  403/login/account 等深色适配 + 全部 ?v=3 防缓存）；**后端零改动**（file_server.py/portal_service.py/config 未变，
  数据与计算与重构前一致）。
- **关键修复**：趋势图右端日期裁切 —— 根因 `boundaryGap:false` 末标签中心压绘图区右缘 + 多序列 `endLabel`
  长中文名不被 containLabel 计算；修复 = `boundaryGap:['5%','5%']` + 移除 endLabel（图例整合进图表头）。
- **布局**：三栏目 100vh 固定视口工作台；表格区内部滚动（thead 原生 sticky，无顶栏重叠）；
  1440×900 首屏 10+ 行完整报价；筛选控件 36px；状态行与标题同行；口径说明可展开。
- **图表**：420px（窄屏 300）；主曲线 2px #7DA7FF；区间带低透明且标注「最低—最高价」；
  多序列配色 dataviz 验证通过（#3987e5/#d95926/#199e70/#c98500/#d55181，对 #151F30 ALL CHECKS PASS）；
  颜色跟随产品不随排名；Tooltip 深色 confine 不越界；同年轴标签 MM-DD、跨年 YYYY-MM-DD。
- **验收**：Playwright 程序化断言 **182 项全 PASS**（4 视口 + 125% 缩放 + 交互 + 布局 + 图表标签包围盒 +
  无横向溢出 + 无 JS 错误）；截图 `data/screenshots/v3/{before,after}/`（各 20 张）。
  脚本：`scripts/verify_portal_v3.py`（断言）、`scripts/shoot_portal.py`（截图）。
- **预览**：8899 实例指向 `/root/smm-portal-v3-static/`（huayou/DevPass2026，dev auth.db）。
  部署方式与 V2 一致：`cp -r /root/smm-portal-v3-static/* static/ && systemctl restart smm-fileserver`（已执行）。
- **已知边界**：旧页面 /today /history /topics /quality /admin 通过令牌自动变暗（未逐一视觉打磨）；
  验收脚本踩坑记录：ECharts 单轴 `convertToPixel` 返回标量（非数组），zrender tspan 的
  transformCoordToGlobal 不含 canvas 页面偏移。

## 八、V4 回归修复（布局/交互/暗色主题，2026-09-07 已部署 ✅）

- **问题与根因**（浏览器 DOM 实测确认）：
  1. 报表两 Tab 混排 —— `hidden` 属性被 `.fill-sec{display:flex}` 覆盖，月报区占位 343px，分类表格仅剩 2 行可见；
     趋势页「自定义起止日期」同理常显。修复：全局 `[hidden]{display:none!important}`（唯一权威开关）。
  2. 趋势页明细表被挤成窄滚动框 —— 按需求删除底部明细表/分页/空容器及仅服务明细的渲染；
     页面 = 标题操作 + 筛选 + 图表（clamp(360px,46vh,480px)，随容器 ResizeObserver 重排）+ 口径；
     单品种图表头新增紧凑摘要行（期末日均价/区间首末变化带实际日期/有效报价点数，数据不足显示—，
     不冒充源字段当日涨跌）；多品种仅图例+Tooltip。
  3. 质量页/运维中心白块+标题不可读 —— 两页仍加载旧浅色 style.css（984 行），白侧栏白卡片黑标题均来自它；
     修复：两页移除 style.css，所需组件在 app.css 用 V3 令牌重写（暗色）；正文对比度 ≥4.5:1 校验通过。
  4. 运维中心信息层级 —— 深色侧栏 210px + SVG 图标（去 emoji）；告警合并为「待处理告警」面板
     （同缺失分类合并展示采集/固定汇总影响，默认 3 条 + 查看全部 N 条；仅展示层，后端判定/条数不变）；
     服务状态紧凑表（Web/SMM采集/最新报价/验证/固定汇总）；系统资源独立面板（磁盘/内存/CPU 真实进度条，
     缺失显未知不填 0，无装饰历史曲线）；总览 2/3+1/3 布局，窄屏自然上下排列；子页全部适配。
- **公共约束落实**：移除 body overflow:hidden 与 100vh 固定视口（趋势/报表回归页面正常纵向滚动）；
  表格 14px 基准；页脚正常文档流；无全局通配符改色；宽表格在自身区域横向滚动；[hidden] 全局生效；
  grid 项 min-width:0 防宽表撑破窄屏。
- **业务回归边界**：仅改静态前端（HTML/CSS/JS）；后端 file_server.py/portal_service.py/ops_monitor.py/
  月均价公式/映射配置/Excel 模板均未改动。
- **验收**：`scripts/verify_regression.py` **202 项断言全 PASS**（1440×900/1920×1080/1366×768/375 + 125% 缩放：
  Tab 互斥且不占位不进焦点、首屏≥8行、分页可达、趋势无明细残留、图表标签/Tooltip/摘要、质量/运维深色与对比度、
  无横向溢出、无新增 JS 错误、**月报 Excel 实际下载并校验**（3 工作表/≥49 业务行/关键字段/组织合并列）。
  截图 `data/screenshots/v4/`（20 张）。
- **仍存在问题**：旧页面 /today /history /topics 仍加载旧 style.css（浅色混排，未在本轮范围，保留可访问）；
  8888/8899 均为新版（8899 预览现直接指向 repo static/）。
