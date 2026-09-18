#!/usr/bin/env bash
# scripts/set_smm_secrets.sh
#
# 在服务器终端交互式写入 SMM 凭据到 /etc/smm-collector/secrets.env
#
# 安全设计：
#   - 必须以 root 运行（写 0600 root:root）
#   - username 正常 prompt；password 用 `read -rs`（不回显）
#   - 关闭本会话历史记录，密码不入 HISTFILE
#   - 二次确认密码（防 typo 锁死账号）
#   - 原子写入（mktemp + mv）；失败自动清理 tmp
#   - 不向终端 echo 任何凭据
#   - 完成后 unset shell 变量（best-effort 内存清除）
#
# 使用：
#   ssh root@106.12.59.96
#   sudo bash /root/smm-lithium-collector/scripts/set_smm_secrets.sh
#
# 轮换凭据：再跑一次即可（覆盖）。
#
# 此脚本本身不含任何凭据，可入 Git。

set -euo pipefail

SECRETS_FILE="/etc/smm-collector/secrets.env"
SECRETS_DIR="/etc/smm-collector"

# ── 权限 ─────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
  echo "[fatal] 必须以 root 运行（sudo bash ...）。" >&2
  exit 1
fi

if [ ! -d "$SECRETS_DIR" ]; then
  echo "[fatal] $SECRETS_DIR 不存在。" >&2
  exit 1
fi

# ── 关闭历史记录（密码不入 HISTFILE） ────────────────────────
HISTCONTROL=ignoreboth
set +o history

cleanup() {
  local exit_code=$?
  set -o history 2>/dev/null || true
  unset SMM_PASSWORD SMM_PASSWORD_CONFIRM 2>/dev/null || true
  [ -n "${TMP_FILE:-}" ] && [ -f "${TMP_FILE:-}" ] && rm -f "$TMP_FILE" 2>/dev/null || true
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

TMP_FILE="$(mktemp "${SECRETS_DIR}/.secrets.env.XXXXXX")"
chmod 600 "$TMP_FILE"
chown root:root "$TMP_FILE"

# ── 收集 username ─────────────────────────────────────────────
echo "==> 写入 SMM 凭据到 ${SECRETS_FILE}"
echo "==> Username（SMM 登录用户名/手机号）:"
read -r SMM_USERNAME

if [ -z "${SMM_USERNAME:-}" ]; then
  echo "[fatal] username 不能为空。" >&2
  exit 1
fi

# ── 收集 password（不回显 + 二次确认） ───────────────────────
echo "==> Password（输入不显示）:"
read -rs SMM_PASSWORD
echo
if [ -z "${SMM_PASSWORD:-}" ]; then
  echo "[fatal] password 不能为空。" >&2
  exit 1
fi

echo "==> 再次输入 Password 以确认:"
read -rs SMM_PASSWORD_CONFIRM
echo
if [ "${SMM_PASSWORD:-}" != "${SMM_PASSWORD_CONFIRM:-}" ]; then
  echo "[fatal] 两次 password 不一致，未写入任何内容。" >&2
  exit 1
fi

# ── 原子写入 ──────────────────────────────────────────────────
{
  echo "# SMM credentials — generated $(date -Iseconds)"
  echo "# Managed by scripts/set_smm_secrets.sh (rerun to rotate)"
  echo "# DO NOT hand-edit; rerun this script instead."
  echo "SMM_USERNAME=${SMM_USERNAME}"
  echo "SMM_PASSWORD=${SMM_PASSWORD}"
} > "$TMP_FILE"

chmod 600 "$TMP_FILE"
chown root:root "$TMP_FILE"
mv -f "$TMP_FILE" "$SECRETS_FILE"
chmod 600 "$SECRETS_FILE"
chown root:root "$SECRETS_FILE"

# ── 摘要（不显示凭据） ────────────────────────────────────────
echo
echo "[ok] 已写入 ${SECRETS_FILE}"
echo "     权限: $(stat -c '%a %U:%G' "$SECRETS_FILE")"
echo "     大小: $(stat -c '%s' "$SECRETS_FILE") 字节"
echo
echo "验证（不显示 password 值）:"
echo "  sudo grep '^SMM_USERNAME=' $SECRETS_FILE"
echo "  sudo grep -c '^SMM_PASSWORD=' $SECRETS_FILE   # 应输出 1"
echo
echo "[note] Claude 看不到你的密码 — 此脚本是你与服务器之间的本地交互。"