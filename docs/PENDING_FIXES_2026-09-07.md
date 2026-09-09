# 待修复问题清单（2026-09-07 记录，供后续修改）

> 本轮 V4 回归修复已部署并通过 202 项浏览器断言。以下为已知遗留问题，按优先级排列。

## 高

1. **旧页面视觉未统一**：`/today` `/history` `/topics` 仍加载旧浅色 `style.css`（984 行），
   与深色主站混排（白卡片/深色标题）。改法明确：移除 style.css 引用，把所需组件类
   （file-table、file-type-badge、completeness-banner 等）迁入 app.css 令牌体系，
   或按 V3/V4 模式整页重写。注意 admin/quality 已迁移，不要误伤。
2. **生产 8888 交互级验证未做**：本轮生产账号密码不可得，8888 验证到
   「文件逐字节一致 + 服务健康 + 页面响应」级别；完整交互验证在 8899（同文件同后端）。
   建议业务侧用 huayou/admin 账号浏览器走查一遍：搜索/筛选/行展开/指标切换/
   月报预览下载/管理员导航。

## 中

3. **quotes 首页表头不再吸顶**：V4 移除 body overflow:hidden/100vh 固定视口后，
   每日报价表回归页面纵向滚动，`.table-scroll`（overflow-x:auto）内的 sticky thead
   在页面滚动下不生效。可选方案：a) 接受现状（表头随页滚动）；b) 双表分离列宽同步；
   c) 恢复 quotes 单页内部滚动（需重新评估「禁止固定高度裁切内容」约束）。
4. **8899 预览实例使用公开 dev 密码**：`huayou/admin / DevPass2026` 暴露于公网 0.0.0.0:8899。
   建议：业务验收完成后关停该实例（`pgrep -f "PORT=8899" | xargs kill`），
   或至少用 FILE_SERVER_PORT 仅监听 127.0.0.1（改 file_server.py 监听地址或加 nginx 限制）。
5. **旧规范日报映射不一致**：`config/business_report_mapping.yaml`（11 列规范日报口径）
   与新 `business_products.yaml` 在硫酸镍/硫酸钴指数、华宝 vs 彭博团队等处不一致，
   新映射为准；旧脚本未改动。如需统一需业务确认后迁移。

## 低

6. **README.md / RUN_GUIDE.md 过时**（Windows/ngrok 时代），可信文档为 CLAUDE.md 与
   DEPLOYMENT_STATUS_*.md；可重写或标注废弃。
7. **LFP 储能型 ≥2.50/≥2.40 无独立报价**（官方并入密度价格点）：门户显示口径说明、
   月报留空，业务确认前维持现状；若需旧口径历史数值需业务另给来源。
8. **验收脚本跨视口重复登录**：verify_regression.py 每次登录一次但多视口共享会话，
   偶发登录按钮点击超时（已用 force=True 缓解）；可改为复用 storage_state。
