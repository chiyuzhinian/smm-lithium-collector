"""dl_link 签名/验签测试（纯函数，无网络、无磁盘依赖）。"""
from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import unquote

from smm_collector import dl_link


def _key_file(tmp_path: Path) -> Path:
    f = tmp_path / "dl_sign.key"
    f.write_text("test-secret-key-0123456789abcdef\n", encoding="utf-8")
    return f


def test_sign_verify_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_DL_KEY", str(_key_file(tmp_path)))
    key = dl_link.load_key()
    assert key == "test-secret-key-0123456789abcdef"
    rel = "2026/10/每日汇总/Excel/SMM锂电业务产品报价_2026-10-20.xlsx"
    exp = int(time.time()) + 3600
    sig = dl_link.sign(rel, exp, key)
    assert dl_link.verify(sig, exp, rel, key) is True


def test_verify_wrong_key_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_DL_KEY", str(_key_file(tmp_path)))
    key = dl_link.load_key()
    rel, exp = "2026/10/x.xlsx", int(time.time()) + 3600
    sig = dl_link.sign(rel, exp, key)
    assert dl_link.verify(sig, exp, rel, "other-key") is False


def test_verify_tampered_path_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_DL_KEY", str(_key_file(tmp_path)))
    key = dl_link.load_key()
    rel, exp = "2026/10/x.xlsx", int(time.time()) + 3600
    sig = dl_link.sign(rel, exp, key)
    assert dl_link.verify(sig, exp, "2026/10/y.xlsx", key) is False
    assert dl_link.verify(sig, exp, "../.env", key) is False


def test_verify_expired_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_DL_KEY", str(_key_file(tmp_path)))
    key = dl_link.load_key()
    rel, exp = "2026/10/x.xlsx", int(time.time()) - 10
    sig = dl_link.sign(rel, exp, key)
    assert dl_link.verify(sig, exp, rel, key) is False


def test_make_url_format_and_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_DL_KEY", str(_key_file(tmp_path)))
    url = dl_link.make_url("http://101.37.235.13", "2026/10/每日汇总/Excel/报价.xlsx", ttl=3600)
    assert url and url.startswith("http://101.37.235.13/dl/")
    sig, exp_s, rest = url.split("/dl/", 1)[1].split("/", 2)
    assert exp_s.isdigit() and int(exp_s) > int(time.time())
    assert "%" in rest  # 非 ASCII 已百分号编码
    key = dl_link.load_key()
    assert dl_link.verify(sig, int(exp_s), unquote(rest), key) is True


def test_make_url_no_key_or_host_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_DL_KEY", str(tmp_path / "missing.key"))
    assert dl_link.make_url("http://h", "2026/10/x.xlsx") is None
    monkeypatch.setenv("FILE_DL_KEY", str(_key_file(tmp_path)))
    assert dl_link.make_url("", "2026/10/x.xlsx") is None


def test_load_key_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_DL_KEY", str(tmp_path / "no_such.key"))
    assert dl_link.load_key() is None
