"""SMM Persistent Browser Profile 排他锁 — SMM Authentication V2 Phase D。

为什么需要锁：
- 同一个 Chromium ``user_data_dir`` 同一时刻只允许一个进程打开；
  第二个进程会因 ``SingletonLock`` 冲突导致 profile 数据库损坏。
- Headless Collector 和 Headed 人工续登不能同时打开同一 profile。

实现：
- 基于 ``fcntl.flock`` 的进程内排他文件锁；
- 锁文件位于 ``auth.profile_lock_path``（默认 ``/var/lock/smm-collector-browser.lock``）；
- 支持 ``acquire(blocking=False)`` / ``release()`` 与 ``with`` 上下文管理。

安全约束：
- 锁文件权限 0600，仅 root/smmweb 可读；
- 不写敏感数据；
- 进程异常退出时内核自动释放锁（flock 行为）。
"""
from __future__ import annotations

import errno
import fcntl
import logging
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger("smm_collector.profile_lock")

DEFAULT_LOCK_PATH = Path("/var/lock/smm-collector-browser.lock")


class ProfileLockError(RuntimeError):
    """Profile 被其他进程占用，无法获取排他锁。"""


def _default_path() -> Path:
    return DEFAULT_LOCK_PATH


class ProfileLock:
    """SMM Browser Profile 排他锁（基于 fcntl.flock）。

    Usage::

        lock = ProfileLock(path)
        with lock:
            ...  # 占用 profile
        # 离开 with 自动释放
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or _default_path())
        self._fd: Optional[int] = None

    def acquire(self, blocking: bool = False) -> bool:
        """尝试获取排他锁。

        Args:
            blocking: True=阻塞等待；False=立即返回。
                本项目默认 False（快速失败，避免等待）。

        Returns:
            True 表示获取成功；False 表示被其他进程占用（仅 blocking=False 时）。

        Raises:
            ProfileLockError: 锁文件无法打开。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                os.chmod(self.path, 0o600)
            except PermissionError as e:
                logger.warning("锁文件 chmod 失败（%s）：%s", self.path, e)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            flags = fcntl.LOCK_EX
            if not blocking:
                flags |= fcntl.LOCK_NB
            fcntl.flock(fd, flags)
        except OSError as e:
            if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                os.close(fd)
                logger.info("profile_lock: 已占用 path=%s", self.path)
                return False
            os.close(fd)
            raise ProfileLockError(f"无法获取 profile 锁 {self.path}：{e}") from e
        self._fd = fd
        # 写持有者信息（PID + 时间），便于人工诊断（不含敏感数据）
        try:
            os.write(fd, f"pid={os.getpid()} time={datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n".encode())
        except Exception:
            pass
        logger.info("profile_lock: 已获取 path=%s pid=%d", self.path, os.getpid())
        return True

    def release(self) -> None:
        """释放锁。无锁时调用为 no-op。"""
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError as e:
            logger.warning("profile_lock 释放失败：%s", type(e).__name__)
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None
        logger.info("profile_lock: 已释放 path=%s", self.path)

    def __enter__(self) -> "ProfileLock":
        if not self.acquire(blocking=False):
            raise ProfileLockError(
                f"SMM browser profile currently in use: {self.path}\n"
                "请确认采集器未运行，或等待其结束。"
            )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


@contextmanager
def profile_lock(path: Optional[Path] = None) -> Iterator[ProfileLock]:
    """``with profile_lock() as lock:`` 形式。"""
    lock = ProfileLock(path)
    try:
        if not lock.acquire(blocking=False):
            raise ProfileLockError(
                f"SMM browser profile currently in use: {lock.path}\n"
                "请确认采集器未运行，或等待其结束。"
            )
        yield lock
    finally:
        lock.release()


def resolve_lock_path(config) -> Path:
    """从 config 读取锁路径；未配置时回退到默认。"""
    settings = getattr(config, "settings", None) or {}
    auth_cfg = settings.get("auth") or {}
    raw = auth_cfg.get("profile_lock_path") or str(DEFAULT_LOCK_PATH)
    return Path(raw)