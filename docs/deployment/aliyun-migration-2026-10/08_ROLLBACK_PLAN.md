# 08 回滚预案

> 日期：2026-10-12 ｜ 覆盖本次迁移的代码/数据/服务回滚路径

## 1. 回滚原则

- 恢复操作全部为「新增/合并」语义：备份只恢复到空目录或 git 不跟踪的 data/ 下，
  服务器上不存在被覆盖的旧文件（新服务器为干净环境）。
- 回滚 = 停服务 + 回到上一代码基线 + 数据保持只增不删。

## 2. 代码回滚

| 场景 | 操作 |
|------|------|
| 迁移修复分支有问题 | 服务器 `cd /root/smm-lithium-collector && git checkout feature/server-migration-2026-09-29`（原迁移分支基线，内容=生产分支+迁移工具）；或 checkout 生产分支 `feature/smm-auth-v2`（313637f） |
| 需要回滚单个修复提交 | `git revert <sha>`（迁移修复共 3 个提交：93a4747 部署脚本、5eb55d6 测试、d925259 sys.path） |
| GitHub 不可达 | 本地 git bundle 已保留在 `/root/migration/smm-repo.bundle` 与本机 temp，可离线重建任意分支 |
| 更新通道 | 服务器访问 GitHub 超时 → 日常更新继续走「本地 commit + push + bundle + scp + 服务器 git pull bundle」 |

## 3. 服务回滚

```bash
# 门户：回退代码后重启
systemctl restart smm-fileserver
# 完全停用（暂回退到无门户状态）
systemctl stop smm-fileserver && systemctl disable smm-fileserver

# nginx：恢复默认站点（如果代理出问题）
ln -sf /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default  # 原 default 已 unlink，可用 apt 重装恢复
unlink /etc/nginx/sites-enabled/ip-access
nginx -t && systemctl reload nginx

# cron：一键移除本次安装的四条任务（不动其它 crontab 内容）
bash /root/smm-lithium-collector/scripts/install_cron.sh --remove
```

## 4. 数据回滚/保护

| 数据 | 保护措施 | 回滚方式 |
|------|---------|---------|
| SQLite 主库 | 恢复自 core 包；包与备份原件保留在 `/root/migration/`（服务器）与 `C:\project\smm-backup\`（本机） | 损坏时从备份包重拷该文件（先停采集与门户） |
| MySQL | dump 保留在 `/root/migration/mysql/smm_lithium.sql`；新库无既有数据被覆盖 | 全库重建：DROP DATABASE smm_lithium 后重新导入（需用户确认） |
| auth.db | 三件套原件保留在备份包 | 停服务后重拷三件套 |
| browser-profile / secrets.env | 备份包原件 | 重拷并恢复 0700/0600 权限 |
| raw 原始存档 | 已恢复 5.4G，备份包保留 | 不需要回滚（只读存档） |

## 5. 系统级回滚（彻底放弃新服务器部署）

1. `systemctl stop smm-fileserver mysql nginx` + disable
2. `bash scripts/install_cron.sh --remove`
3. 删除 `/root/smm-lithium-collector`、`/var/lib/smm-collector`、`/var/lib/smm-fileserver`、
   `/etc/smm-collector`、`/etc/systemd/system/smm-fileserver.service`、nginx 站点文件、
   MySQL 库 smm_lithium（**以上删除均需用户逐项确认，且备份原件始终保留在本机**）
4. 服务器上 Docker/阿里云组件/系统包不动（安装的 apt 包保留无害）

## 6. 回滚决策点

- 触发条件：门户数据异常且无法定位；采集连续失败（consecutive_failures≥2）且非登录态问题；
  用户要求回退。
- 执行人：用户 + Claude Code；任何删除操作前必须先展示将被删除的路径清单并获得确认。
