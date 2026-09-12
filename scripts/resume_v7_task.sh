#!/usr/bin/env bash
# ============================================================
# SMM 锂电 - V6/V7 任务 resume 脚本
# ============================================================
# 用途：在 Claude 任务中断（额度耗尽等）后，重新进入工作状态。
# 用法：bash scripts/resume_v7_task.sh
# 然后告诉 Claude："继续执行 RESUME.md 中的剩余任务"
# ============================================================
set -e

cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"

echo "=== 1. 当前 Git 状态 ==="
git branch --show-current
echo
git status --short
echo
echo "HEAD: $(git rev-parse HEAD)"
echo
echo "=== 2. 最近 5 个提交 ==="
git log --oneline -5
echo
echo "=== 3. 已完成 ==="
echo "✅ Step 1: 后端 - portal_service.py 新增 catalog 函数（catalog_products/catalog_quotes/catalog_natural_id）"
echo "✅ Step 2: 后端 - file_server.py 新增 /api/portal/catalog/products 与 /api/portal/catalog/quotes 路由"
echo "✅ Checkpoint: 已提交 (d522fda feat(backend): 新增全量采集产品目录 API)"
echo
echo "=== 4. 实测验证 ==="
.venv/bin/python -c "
import sqlite3, sys
sys.path.insert(0, 'src')
from smm_collector import portal_service as ps
con = sqlite3.connect('file:data/database/smm_lithium.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row
cat = ps.catalog_products(con)
print(f'全量产品数：{cat[\"meta\"][\"total\"]}')
print(f'分类数：{len(cat[\"categories\"])}')
pvdf = [p for p in cat['products'] if p['category'] == 'PVDF' and '三元' in p['specification']]
print(f'PVDF 用于三元正极材料：{pvdf[0][\"id\"] if pvdf else \"未找到\"}')
con.close()
" 2>&1
echo
echo "=== 5. 剩余任务 ==="
echo "⏳ Step 3: 前端 - static/index.html (微调选择器/布局)"
echo "⏳ Step 4: 前端 - static/js/quotes.js 改造："
echo "     - 加载 /api/portal/catalog/products"
echo "     - 改造下拉选择器（搜索+多选+chip）"
echo "     - 切换默认/已选展示逻辑"
echo "⏳ Step 5: 前端 - static/trends.html (单 chart → chart-grid)"
echo "⏳ Step 6: 前端 - static/js/trends.js 改造："
echo "     - N 个独立 ECharts 实例"
echo "     - 每个 chart card 独立 Y 轴 scale: true"
echo "     - dispose 旧实例 + ResizeObserver"
echo "⏳ Step 7: 样式 - static/css/app.css 新增 catalog 下拉、chart-grid、chart-card 样式"
echo "⏳ Step 8: 测试 - tests/test_portal_service.py 新增 catalog 测试"
echo "⏳ Step 9: pytest 全量回归验证"
echo "⏳ Step 10: 浏览器验收（如环境支持）"
echo "⏳ Step 11: 报告完成并等待用户确认部署"
echo
echo "=== 6. 关键设计约束 ==="
echo "- 每日报价默认产品保持不变（无选择时仍展示 40 条 SMM 业务品种）"
echo "- 不污染 business_products.yaml（仍 49 条业务品种）"
echo "- catalog id = SHA-256(自然键)[:12] hex + cp_ 前缀"
echo "- 数据真实性：只展示 valid 数据，缺报不补点、不伪造"
echo "- 富宝数据库未发现（仅 SMM 一类 source），不新增外部源"
echo "- 趋势页产品池保持 40 条业务品种不变"
echo "- 价格走势 Y 轴独立 scale: true 解决量级差异"
echo
echo "=== 7. 不要做的事 ==="
echo "❌ 改 Nginx / Docker / systemd / 网络配置"
echo "❌ 修改业务映射 business_products.yaml 的产品身份"
echo "❌ 触碰数据与报表 / 月度报表 / 内部价格 / 收藏功能"
echo "❌ 自动重启 smm-fileserver 服务（先告诉用户）"
echo "❌ git reset --hard / git clean -fd 除非用户明确授权"
echo
echo "=== 8. 完整计划文件 ==="
echo "plan: /root/.claude/plans/cryptic-foraging-twilight.md"
echo "当前基线: 6378675 (feature/auth-admin-dashboard 起点)"
echo "当前 HEAD: $(git rev-parse HEAD)"
