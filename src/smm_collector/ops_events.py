"""运维事件写入（Web 服务与采集器共用）。

采集器（root cron）与 Web 服务（smmweb）会向同一 auth.db 写入事件。
本模块**异常安全**：任何失败只返回 False，绝不抛出异常影响采集流程。
DDL 与 web_auth.SCHEMA 中的 ops_events 表保持一致。
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime

DEFAULT_DB_PATH = "/var/lib/smm-fileserver/auth.db"

_OPS_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS ops_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    level      TEXT NOT NULL DEFAULT 'info',
    message    TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_created ON ops_events(created_at);
"""


def db_path() -> str:
    """auth.db 路径（环境变量可覆盖，测试用）。"""
    return os.getenv("FILE_SERVER_AUTH_DB", DEFAULT_DB_PATH)


def _conn(path: str | None = None):
    conn = sqlite3.connect(path or db_path(), timeout=5)
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def ensure_schema(path: str | None = None) -> bool:
    """引导 ops_events 表（可独立于 Web 服务调用）。"""
    try:
        with _conn(path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_OPS_EVENTS_DDL)
        return True
    except Exception:
        return False


def write_event(event_type: str, level: str = "info", message: str = "",
                detail: str = "", path: str | None = None) -> bool:
    """写入一条运维事件；任何异常都吞掉并返回 False（绝不影响采集）。"""
    try:
        with _conn(path) as conn:
            conn.executescript(_OPS_EVENTS_DDL)
            conn.execute(
                "INSERT INTO ops_events(event_type, level, message, detail, created_at)"
                " VALUES(?,?,?,?,?)",
                (event_type, level, message, detail,
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        return True
    except Exception:
        return False


def recent_events(limit: int = 50, path: str | None = None) -> list[dict]:
    try:
        with _conn(path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT event_type, level, message, detail, created_at FROM ops_events"
                " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
