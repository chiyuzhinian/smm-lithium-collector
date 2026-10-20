# 07 部署计划（2026-10-20，待「确认部署」后执行）

## 前置条件

- 功能分支 `feature/dingtalk-business-report-2026-10` 已推送 GitHub，本地测试全绿
- 服务器生产现状：阿里云 ECS 101.37.235.13，/root/smm-lithium-collector，生产分支 feature/aliyun-migration-2026-10

## 部署步骤

1. **备份**
   - `cd /root/smm-lithium-collector && git rev-parse HEAD` 记录当前 SHA（回滚点）
   - `mkdir -p backups/dingtalk-report-pre-20261020 && cp src/smm_collector/notifier.py src/smm_collector/main.py src/smm_collector/ops_events.py scripts/file_server.py backups/dingtalk-report-pre-20261020/`
   - 确认 SQLite/MySQL 备份在案（本次改动只读数据库，不覆盖任何数据）
2. **代码通道**（既有 bundle 通道，服务器直连 GitHub 超时）
   - 本地：`git bundle create dingtalk-report.bundle feature/dingtalk-business-report-2026-10`
   - `scp dingtalk-report.bundle root@101.37.235.13:/root/smm-lithium-collector/`
   - 服务器：`git fetch ./dingtalk-report.bundle feature/dingtalk-business-report-2026-10 && git checkout -B feature/dingtalk-business-report-2026-10 FETCH_HEAD`（或 `git pull ./dingtalk-report.bundle <branch>`）
3. **一次性密钥**
   ```bash
   openssl rand -hex 32 > /var/lib/smm-fileserver/dl_sign.key
   chown root:smmweb /var/lib/smm-fileserver/dl_sign.key
   chmod 640 /var/lib/smm-fileserver/dl_sign.key
   ```
4. **测试**：`.venv/bin/python -m pytest -q`（服务器 venv 为权威基准）
5. **重启门户**：`systemctl restart smm-fileserver`（仅因 file_server.py 改动）
   - **不动**：cron、supervisor、采集流程、浏览器登录态、MySQL 同步
6. **受控验证（先不真发群）**
   - `DINGTALK_DRY_RUN=1 bash scripts/run_daily.sh` 或直接以现有数据只读生成日报（dry-run 不发送）
   - 真实库只读对账脚本：40 行与 `GET /api/portal/quotes` 逐项比对（身份/名称/规格/类别/最低/最高/日均/涨跌/单位/报价日期）
   - 签名链接 curl：有效期内 200；篡改 sig / 过期 / 无签名 → 404
7. **正式群发送**：需用户明确确认，并标注测试消息；随后观察下一次 cron 自动采集（工作日 9:05/9:30）的正式日报

## 验收清单（正式日报）

- [ ] 顶部只有采集结果 + 全量数据，无 MySQL 新增/更新
- [ ] 40 产品按 7 个业务类别完整展示，价格/规格/单位与网站一致
- [ ] 报价日期真实；缺失产品状态准确（本次无新报价 + 最近真实报价/历史暂无）
- [ ] 两个下载链接可点击直下（免登录）
- [ ] 无重复推送
