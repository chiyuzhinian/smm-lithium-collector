"""Profile Lock 单元测试 — SMM Auth V2 Phase D。

策略：
- 使用真实 flock（fcntl）但全部在 tmp_path 操作
- 同一进程内：通过 ProfileLock 实例可重入释放
- 多进程：用 multiprocessing 模拟 collector 和人工续登互斥
- 验证：进程1 占用 → 进程2 被拒；进程1 退出 → 进程2 可获取
"""
from __future__ import annotations

import multiprocessing
import os
import time
from pathlib import Path

import pytest

from smm_collector.profile_lock import (
    DEFAULT_LOCK_PATH,
    ProfileLock,
    ProfileLockError,
    profile_lock,
    resolve_lock_path,
)


# ── Fixtures & helpers ──────────────────────────────────────


def _lock_path(tmp_path: Path) -> Path:
    return tmp_path / "test.lock"


def _child_holds_lock_then_exits(lock_path_str: str, hold_seconds: float,
                                  result_queue: multiprocessing.Queue) -> None:
    """子进程：获取锁后等待 N 秒，然后释放。"""
    lock_path = Path(lock_path_str)
    lock = ProfileLock(lock_path)
    acquired = lock.acquire(blocking=False)
    result_queue.put(("acquired", acquired))
    if not acquired:
        return
    try:
        time.sleep(hold_seconds)
        result_queue.put(("held", True))
    finally:
        lock.release()
    result_queue.put(("released", True))


def _child_tries_lock(lock_path_str: str, wait_seconds: float,
                      result_queue: multiprocessing.Queue) -> None:
    """子进程：尝试立即获取锁（应被拒）。"""
    lock_path = Path(lock_path_str)
    lock = ProfileLock(lock_path)
    acquired = lock.acquire(blocking=False)
    result_queue.put(("acquired", acquired))
    if not acquired:
        return
    try:
        time.sleep(wait_seconds)
    finally:
        lock.release()


# ── 基本锁行为 ─────────────────────────────────────────────


def test_lock_path_default():
    """DEFAULT_LOCK_PATH 是合理位置（不在项目内）。"""
    p = DEFAULT_LOCK_PATH
    assert "smm" in str(p).lower()
    assert not str(p).startswith("/root/smm-lithium-collector")  # 项目外


def test_resolve_lock_path_with_config():
    """resolve_lock_path 读 config.auth.profile_lock_path。"""

    class _Cfg:
        def __init__(self, auth):
            self.settings = {"auth": auth}

    p = resolve_lock_path(_Cfg({"profile_lock_path": "/tmp/foo.lock"}))
    assert str(p) == "/tmp/foo.lock"

    p2 = resolve_lock_path(_Cfg({}))
    assert p2 == DEFAULT_LOCK_PATH


def test_acquire_release(tmp_path):
    """同一进程内：acquire → release → 可再次 acquire。"""
    lock = ProfileLock(_lock_path(tmp_path))
    assert lock.acquire(blocking=False) is True
    lock.release()
    # 可再次获取
    assert lock.acquire(blocking=False) is True
    lock.release()


def test_lock_file_mode(tmp_path):
    """锁文件权限 0600。"""
    lock_path = _lock_path(tmp_path)
    lock = ProfileLock(lock_path)
    lock.acquire(blocking=False)
    lock.release()
    mode = lock_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_lock_file_content_no_secrets(tmp_path):
    """锁文件内容仅含 PID + 时间，不含敏感数据。"""
    lock_path = _lock_path(tmp_path)
    lock = ProfileLock(lock_path)
    lock.acquire(blocking=False)
    lock.release()
    content = lock_path.read_text()
    # 仅允许 pid= / time=
    assert "pid=" in content
    assert "time=" in content
    # 不应含 cookie/token/password
    for forbidden in ("cookie", "token", "password", "session"):
        assert forbidden not in content.lower()


# ── with 上下文管理器 ──────────────────────────────────────


def test_with_statement_releases(tmp_path):
    """with 语句退出自动释放。"""
    lock_path = _lock_path(tmp_path)
    with ProfileLock(lock_path):
        # 持有中：再获取应失败
        other = ProfileLock(lock_path)
        assert other.acquire(blocking=False) is False
    # 退出后：可获取
    other = ProfileLock(lock_path)
    assert other.acquire(blocking=False) is True
    other.release()


def test_with_statement_raises_when_locked(tmp_path):
    """锁被占用时进入 with 抛 ProfileLockError。"""
    lock_path = _lock_path(tmp_path)
    # 进程内先占
    holder = ProfileLock(lock_path)
    holder.acquire(blocking=False)
    try:
        # 再进 with 应抛
        with pytest.raises(ProfileLockError) as exc:
            with ProfileLock(lock_path):
                pass
        assert "currently in use" in str(exc.value)
    finally:
        holder.release()


def test_context_manager_helper(tmp_path):
    """profile_lock() 同样行为。"""
    lock_path = _lock_path(tmp_path)
    holder = ProfileLock(lock_path)
    holder.acquire(blocking=False)
    try:
        with pytest.raises(ProfileLockError):
            with profile_lock(lock_path):
                pass
    finally:
        holder.release()


# ── 多进程互斥 ─────────────────────────────────────────────


def test_multiprocess_mutex(tmp_path):
    """进程1占锁 → 进程2被拒（non-blocking）。"""
    lock_path = _lock_path(tmp_path)
    q: multiprocessing.Queue = multiprocessing.Queue()

    # 进程1：获取锁并持有 1 秒
    p1 = multiprocessing.Process(
        target=_child_holds_lock_then_exits,
        args=(str(lock_path), 1.0, q),
    )
    p1.start()
    time.sleep(0.3)  # 等子进程拿到锁

    # 进程2：尝试立即获取
    p2 = multiprocessing.Process(
        target=_child_tries_lock,
        args=(str(lock_path), 0.1, q),
    )
    p2.start()

    p1.join(timeout=5)
    p2.join(timeout=5)

    # 收集结果
    results = []
    while not q.empty():
        results.append(q.get(timeout=1))

    # 进程1 应 acquired/held；进程2 应 acquired=False
    acquired_flags = [r[1] for r in results if r[0] == "acquired"]
    assert acquired_flags[0] is True   # 进程1 成功
    assert acquired_flags[1] is False  # 进程2 被拒


def test_multiprocess_release_then_acquire(tmp_path):
    """进程1释放 → 进程2可获取（验证锁能被释放）。"""
    lock_path = _lock_path(tmp_path)
    q: multiprocessing.Queue = multiprocessing.Queue()

    # 进程1：短暂持有后立即释放
    p1 = multiprocessing.Process(
        target=_child_holds_lock_then_exits,
        args=(str(lock_path), 0.3, q),
    )
    p1.start()
    p1.join(timeout=3)
    # 此时锁已释放

    # 进程2：应能获取
    p2 = multiprocessing.Process(
        target=_child_tries_lock,
        args=(str(lock_path), 0.1, q),
    )
    p2.start()
    p2.join(timeout=3)

    results = []
    while not q.empty():
        results.append(q.get(timeout=1))

    # 两个进程都应 acquired=True
    acquired_flags = [r[1] for r in results if r[0] == "acquired"]
    assert acquired_flags == [True, True]


# ── 路径解析健壮性 ─────────────────────────────────────────


def test_resolve_lock_path_none_settings():
    """config.settings 为 None/缺省时回退默认。"""

    class _Cfg:
        settings = None

    # 不抛异常
    p = resolve_lock_path(_Cfg())
    assert p == DEFAULT_LOCK_PATH