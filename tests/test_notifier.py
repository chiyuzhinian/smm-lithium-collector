"""钉钉发送传输层测试：失败重试 / 防重复发送 / DRY_RUN / 事件记录。

不访问真实网络：httpx.AsyncClient 被替换为假客户端；auth.db 用临时文件。
"""
from __future__ import annotations

import sqlite3

import pytest

from smm_collector import notifier
from smm_collector import ops_events

META = {
    "run_id": "run-abc-123", "status": "success", "target_date": "2026-10-20",
    "data_date": "2026-10-20",
    "success_categories": [f"c{i}" for i in range(39)],
    "expected_categories": [f"c{i}" for i in range(40)],
    "total_clean_rows": 450,
}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(notifier, "DINGTALK_WEBHOOK", "https://oapi.dingtalk.example/robot/send?access_token=t")
    monkeypatch.setattr(notifier, "DINGTALK_SECRET", "sec")
    monkeypatch.setenv("FILE_SERVER_AUTH_DB", str(tmp_path / "auth_test.db"))
    monkeypatch.setattr(notifier, "SEND_BACKOFF_SECONDS", [0, 0])
    yield


class _FakeResp:
    def __init__(self, errcode=0, status_code=200):
        self.status_code = status_code
        self._errcode = errcode

    def json(self):
        return {"errcode": self._errcode}


class _FakeClient:
    """工厂：每次 __init__ 都产生一个可计数的假客户端。"""

    instances = []

    def __init__(self, *a, **k):
        self.calls = 0
        self.fail_mode = "raise"  # raise / errcode / ok
        _FakeClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        self.calls += 1
        if self.fail_mode == "raise":
            raise RuntimeError("network down")
        if self.fail_mode == "errcode":
            return _FakeResp(errcode=300001, status_code=200)
        return _FakeResp(errcode=0)


def _patch_client(monkeypatch, fail_mode):
    _FakeClient.instances = []
    monkeypatch.setattr(notifier.httpx, "AsyncClient",
                        lambda **k: _new_client(fail_mode))
    return _FakeClient.instances


def _new_client(fail_mode):
    c = _FakeClient()
    c.fail_mode = fail_mode
    return c


@pytest.mark.asyncio
async def test_send_failure_retries_three_times(monkeypatch):
    instances = _patch_client(monkeypatch, "raise")
    ok = await notifier.send_dingtalk("t", "text")
    assert ok is False
    assert len(instances) == notifier.SEND_ATTEMPTS == 3
    assert sum(c.calls for c in instances) == 3


@pytest.mark.asyncio
async def test_send_errcode_retries_then_fail(monkeypatch):
    instances = _patch_client(monkeypatch, "errcode")
    ok = await notifier.send_dingtalk("t", "text")
    assert ok is False
    assert sum(c.calls for c in instances) == 3


@pytest.mark.asyncio
async def test_send_success_single_call(monkeypatch):
    instances = _patch_client(monkeypatch, "ok")
    ok = await notifier.send_dingtalk("t", "text")
    assert ok is True
    assert sum(c.calls for c in instances) == 1


@pytest.mark.asyncio
async def test_daily_notification_writes_sent_event(monkeypatch):
    _patch_client(monkeypatch, "ok")
    ok = await notifier.send_daily_notification(META)
    assert ok is True
    assert ops_events.has_event(notifier.SENT_EVENT, "run_id=run-abc-123")


@pytest.mark.asyncio
async def test_daily_notification_dedup_same_run(monkeypatch):
    _patch_client(monkeypatch, "ok")
    assert await notifier.send_daily_notification(META) is True
    # 第二次调用（同一 run_id）→ 命中防重标记，不再发送
    instances = _patch_client(monkeypatch, "ok")
    ok = await notifier.send_daily_notification(META)
    assert ok is True
    assert sum(c.calls for c in instances) == 0


@pytest.mark.asyncio
async def test_daily_notification_partial_failure_event(monkeypatch):
    async def _half_fail(title, text):
        return False

    monkeypatch.setattr(notifier, "send_dingtalk", _half_fail)
    ok = await notifier.send_daily_notification(META)
    assert ok is False
    assert ops_events.has_event(notifier.FAILED_EVENT, "run_id=run-abc-123")
    # 部分失败不写 SENT 标记（同一 run 重试仍允许补发）
    assert not ops_events.has_event(notifier.SENT_EVENT, "run_id=run-abc-123")


@pytest.mark.asyncio
async def test_dry_run_does_not_send(monkeypatch):
    _patch_client(monkeypatch, "ok")
    monkeypatch.setenv("DINGTALK_DRY_RUN", "1")
    ok = await notifier.send_daily_notification(META)
    assert ok is True
    assert sum(c.calls for c in _FakeClient.instances) == 0
    # dry-run 也写 SENT 标记？不写——dry-run 不视为真实发送成功
    assert not ops_events.has_event(notifier.SENT_EVENT, "run_id=run-abc-123")


@pytest.mark.asyncio
async def test_no_webhook_returns_false(monkeypatch):
    monkeypatch.setattr(notifier, "DINGTALK_WEBHOOK", "")
    monkeypatch.delenv("DINGTALK_DRY_RUN", raising=False)
    ok = await notifier.send_daily_notification(META)
    assert ok is False


def test_ops_events_has_event_matching():
    ops_events.write_event("EVT_X", "info", "hello run_id=zzz")
    assert ops_events.has_event("EVT_X", "run_id=zzz") is True
    assert ops_events.has_event("EVT_X", "run_id=other") is False
    assert ops_events.has_event("EVT_Y", "run_id=zzz") is False


@pytest.mark.asyncio
async def test_daily_title_without_smm_prefix(monkeypatch):
    titles = []

    async def _rec(title, text):
        titles.append(title)
        return True

    monkeypatch.setattr(notifier, "send_dingtalk", _rec)
    ok = await notifier.send_daily_notification(META)
    assert ok is True
    assert titles == ["锂电日报 2026-10-20"]
