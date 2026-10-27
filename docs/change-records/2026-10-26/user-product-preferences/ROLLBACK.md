# ROLLBACK — 回滚方案

## 触发条件

生产验收失败（三页偏好/过滤/走势任一异常且短时无法修复）、服务异常、或其他需要回退的场景。

## 回滚步骤（服务器）

```bash
cd /root/smm-lithium-collector
git checkout -B feature/price-domain-dingtalk-report-2026-10 c00ab8b   # 回到部署前基线
# 或：git revert <本次变更 commit>
systemctl restart smm-fileserver
```

前端静态文件随同次重启生效；nginx / 证书 / cron 无需任何操作。

## auth.db 数据策略

- **默认保留** `user_preferences` 表与数据：无害、可审计；重上功能时直接可用；
- 如需彻底清除（可选）：
  ```bash
  sqlite3 /var/lib/smm-fileserver/auth.db "DROP TABLE user_preferences;"
  ```
  执行前建议先备份：`cp /var/lib/smm-fileserver/auth.db /var/lib/smm-fileserver/auth.db.bak-$(date +%F)`

## 回滚后的行为

- 三页回到「刷新即丢」的原行为；每日报价恢复显示「暂无数据」行；走势摘要恢复「期末日均价」；
- 无任何历史价格数据、采集配置、任务受影响（本次未改动这些模块）。

## 验证

回滚后：登录 → 三页各刷新一次确认无 JS 报错 → 每日报价确认「暂无数据」行恢复 → 钉钉日报次日正常。
