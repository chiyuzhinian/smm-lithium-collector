# 02 新服务器环境审计报告（Phase 1）

> 日期：2026-10-12 ｜ 目标：阿里云 ECS `101.37.235.13`
> 状态：**✅ 已完成（全部只读检查，服务器零改动）**
> 认证：root 密码（用户提供，未写入任何报告/日志；未安装任何持久凭据）

## 1. 实例自证

| 项 | 结果 |
|----|------|
| 公网 IP 自证 | ✅ 阿里云元数据 `eipv4` = `101.37.235.13`，与任务书一致 |
| 主机名 | `iZbp18jpznoxyxwt8t7umgZ`（阿里云 ECS） |
| 操作系统 | ✅ Ubuntu 22.04.5 LTS（内核 5.15.0-191-generic） |
| CPU | 4 核 |
| 内存 | 7.3 GiB（约 8GB）✅ |
| 磁盘 | /dev/vda3 79G，已用 3.6G，**可用 72G** ✅（需求约 8G：备份解压 5.4G + venv/Chromium ~1G + MySQL ~0.3G + 余量） |
| 时区 | ✅ Asia/Shanghai（CST +0800，2026-10-12 14:13） |
| Swap | 无（可选加 2G swap 提升 Chromium 采集稳定性，非必需） |

## 2. 软件与冲突检查

| 项 | 结果 |
|----|------|
| Python | 系统 `python3.10.12`；**官方源有 `python3.11`（候选 3.11.0~rc1）可安装**；无 python3.12（需 deadsnakes PPA，默认不引入第三方源） |
| MySQL / Nginx | ❌ 均未安装（无冲突，可自由安装） |
| 面板（宝塔/1Panel） | ❌ 未安装 ✅ |
| Docker | ⚠️ 已安装并运行（docker-ce 29.9.0 + compose，含 containerd）。**按用户要求：价格系统不使用 Docker，Docker 环境保持原样不动** |
| 阿里云系服务 | aegis（安骑士）、aliyun assist、hbrclient（云备份）运行中——正常，不动 |
| pip 源 | ✅ 已配置阿里云内网镜像（`mirrors.cloud.aliyuncs.com`，root 级 pip.conf + pydistutils.cfg），venv 安装依赖将走内网 |
| apt 源 | ✅ 阿里云内网镜像（jammy 全组件） |

## 3. 端口 / 防火墙 / 定时任务

| 项 | 结果 |
|----|------|
| 监听端口 | 仅 `22`（sshd，0.0.0.0 + [::]）✅ 80/443/8888/3306 全部空闲 |
| ufw | inactive（服务器内无防火墙） |
| iptables | INPUT policy ACCEPT，仅有 Docker 链（FORWARD DROP 为 Docker 默认） |
| 安全组 | 服务器内不可见，需阿里云控制台确认（计划：22/80 开放，8888/3306 不开放） |
| crontab（root） | 空 ✅ 无其它项目任务，安装采集 cron 不会误删他项 |
| /root 既有内容 | `lianghe/`、`workspace/`（均为空目录，2026-10-12 创建）、`.Xauthority`（存在 X11 转发痕迹，Phase 6 人工登录或可复用）、`.ssh/`。**一律不动** |
| /opt | alibabacloud、containerd（不动） |

## 4. 结论

**新服务器为干净环境，与价格系统部署无任何冲突**。部署路径：
- Python 3.11（官方源）→ venv（pip 走阿里云内网镜像）
- MySQL 8.0（apt mysql-server，与 dump 8.0.46 兼容）
- Nginx（apt，80 → 127.0.0.1:8888 代理）
- 门户经 nginx 对外，8888 绑 127.0.0.1 不直连公网
- 磁盘充裕，无需清理；Docker 不动

唯一外部依赖：阿里云**安全组需放行 80**（控制台操作，部署完成后由用户执行或授权）。
