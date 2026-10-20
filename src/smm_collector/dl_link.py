"""公开下载链接签名（钉钉日报 Excel 下载专用）。

免登录、限时（默认 7 天）、路径绑定：签名 = HMAC-SHA256(key, f"{relpath}|{exp}")。
- 生成方：采集进程（business_daily 生成钉钉日报里的两个 Excel 下载链接）
- 校验方：门户 file_server 的 GET /dl/{sig}/{exp}/{relpath} 公开路由

密钥文件默认 /var/lib/smm-fileserver/dl_sign.key（FILE_DL_KEY 环境变量可覆盖）。
密钥缺失时：生成方回退为登录式链接（business_daily 处理），校验方一律 404（绝不故障开放）。

安全边界：
  - 签名绑定相对路径，未持密钥者无法伪造其他文件路径；
  - 过期时间戳限制有效期；
  - 签名比对用 hmac.compare_digest（常量时间）。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from pathlib import Path
from urllib.parse import quote

DEFAULT_KEY_PATH = "/var/lib/smm-fileserver/dl_sign.key"
DEFAULT_TTL_SECONDS = 7 * 24 * 3600


def load_key() -> str | None:
    """读取签名密钥（去首尾空白）。缺失/不可读返回 None。"""
    key_path = os.getenv("FILE_DL_KEY", DEFAULT_KEY_PATH)
    try:
        key = Path(key_path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return key or None


def sign(relpath: str, exp: int, key: str) -> str:
    """相对路径 + 过期时间戳 → hex 签名。"""
    msg = f"{relpath}|{exp}".encode("utf-8")
    return hmac.new(key.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify(sig: str, exp: int, relpath: str, key: str) -> bool:
    """验签：签名一致（常量时间比较）且未过期。"""
    if exp <= int(time.time()):
        return False
    return hmac.compare_digest(sign(relpath, exp, key), sig or "")


def make_url(file_host: str, relpath: str, ttl: int = DEFAULT_TTL_SECONDS) -> str | None:
    """生成公开签名下载链接 {host}/dl/{sig}/{exp}/{quote(relpath)}。

    密钥缺失或 host 为空 → 返回 None（调用方回退登录式链接）。
    relpath 须为 exports 根内相对路径（不含协议头）；URL 中非 ASCII 段做百分号编码，
    校验方（file_server）先 unquote 再验签，签名始终针对未编码的 relpath。
    """
    key = load_key()
    if not key or not file_host:
        return None
    rel = relpath.strip("/")
    exp = int(time.time()) + ttl
    sig = sign(rel, exp, key)
    return f"{file_host.rstrip('/')}/dl/{sig}/{exp}/{quote(rel, safe='/')}"
