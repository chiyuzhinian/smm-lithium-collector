"""MySQL 数据库连接管理和表操作。

负责：连接/断开、自动建库建表、批量 upsert、异常记录写入、同步记录管理。
不负责：同步流程编排（见 synchronizer.py）。
"""
from __future__ import annotations
import hashlib, logging, os, time
from datetime import datetime
from decimal import Decimal
from typing import Any

log = logging.getLogger("smm_collector.mysql")

# ── MySQL 配置 ──────────────────────────────────────────────────

def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


MYSQL_CONFIG = {
    "host": _env("MYSQL_HOST", "127.0.0.1"),
    "port": int(_env("MYSQL_PORT", "3306")),
    "user": _env("MYSQL_USER", "root"),
    "password": _env("MYSQL_PASSWORD", ""),
    "database": _env("MYSQL_DATABASE", "smm_lithium"),
    "charset": _env("MYSQL_CHARSET", "utf8mb4"),
    "connect_timeout": int(_env("MYSQL_CONNECT_TIMEOUT", "10")),
    "autocommit": False,
}
SYNC_BATCH_SIZE = int(_env("MYSQL_SYNC_BATCH_SIZE", "500"))
SYNC_MAX_RETRIES = int(_env("MYSQL_SYNC_MAX_RETRIES", "3"))
SYNC_RETRY_INTERVAL = int(_env("MYSQL_SYNC_RETRY_INTERVAL", "5"))
AUTO_CREATE_DB = _env("MYSQL_AUTO_CREATE_DATABASE", "true").lower() == "true"
AUTO_SYNC = _env("MYSQL_AUTO_SYNC_AFTER_COLLECTION", "true").lower() == "true"


class MySQLError(RuntimeError):
    """MySQL 相关异常。"""


# ── 建表 SQL ──────────────────────────────────────────────────

CREATE_PRICE_TABLE = """
CREATE TABLE IF NOT EXISTS smm_price_records (
    id INT AUTO_INCREMENT PRIMARY KEY,
    source VARCHAR(32) NOT NULL COMMENT '数据来源',
    market VARCHAR(64) NOT NULL COMMENT '市场',
    category VARCHAR(64) NOT NULL COMMENT '分类',
    product_name VARCHAR(128) NOT NULL COMMENT '品名',
    specification VARCHAR(256) NOT NULL DEFAULT '' COMMENT '规格',
    min_price DECIMAL(20, 4) DEFAULT NULL COMMENT '最低价',
    max_price DECIMAL(20, 4) DEFAULT NULL COMMENT '最高价',
    average_price DECIMAL(20, 4) DEFAULT NULL COMMENT '平均价',
    change_value DECIMAL(20, 4) DEFAULT NULL COMMENT '涨跌',
    unit VARCHAR(32) NOT NULL DEFAULT '' COMMENT '单位',
    price_date DATE NOT NULL COMMENT '价格日期',
    collected_at DATETIME NOT NULL COMMENT '采集时间',
    source_url VARCHAR(512) DEFAULT NULL COMMENT '来源URL',
    collection_method VARCHAR(32) DEFAULT NULL COMMENT '采集方式',
    raw_text TEXT DEFAULT NULL COMMENT '原始行文本',
    extra_fields TEXT DEFAULT NULL COMMENT '额外字段JSON',
    record_hash VARCHAR(64) NOT NULL COMMENT 'SHA256记录哈希',
    validation_status VARCHAR(16) DEFAULT NULL COMMENT '校验状态:valid/warning/invalid',
    validation_message TEXT DEFAULT NULL COMMENT '校验信息',
    created_at DATETIME NOT NULL COMMENT '创建时间',
    updated_at DATETIME NOT NULL COMMENT '更新时间',
    UNIQUE KEY uq_record (record_hash),
    KEY idx_price_date (price_date),
    KEY idx_category (category),
    KEY idx_product_name (product_name),
    KEY idx_category_date (category, price_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='SMM锂电现货价格主表';
"""

CREATE_QUALITY_TABLE = """
CREATE TABLE IF NOT EXISTS smm_data_quality_issues (
    id INT AUTO_INCREMENT PRIMARY KEY,
    record_hash VARCHAR(64) NOT NULL COMMENT '关联价格记录的hash',
    source_record_id INT DEFAULT NULL COMMENT '关联smm_price_records.id',
    issue_type VARCHAR(32) NOT NULL COMMENT '异常类型',
    issue_level VARCHAR(16) NOT NULL DEFAULT 'warning' COMMENT 'warning/error',
    field_name VARCHAR(64) DEFAULT NULL COMMENT '异常字段名',
    original_value TEXT DEFAULT NULL COMMENT '原始值',
    processed_value TEXT DEFAULT NULL COMMENT '处理后的值',
    issue_message TEXT NOT NULL COMMENT '异常描述',
    price_date DATE DEFAULT NULL COMMENT '价格日期',
    detected_at DATETIME NOT NULL COMMENT '检测时间',
    processing_status VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT 'pending/accepted/corrected/ignored',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_hash (record_hash),
    KEY idx_level_date (issue_level, price_date),
    KEY idx_status (processing_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='数据质量问题记录表';
"""

CREATE_SYNC_TABLE = """
CREATE TABLE IF NOT EXISTS smm_sync_runs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    sync_batch_id VARCHAR(64) NOT NULL COMMENT '同步批次ID',
    sync_started_at DATETIME NOT NULL COMMENT '开始时间',
    sync_finished_at DATETIME DEFAULT NULL COMMENT '结束时间',
    source_database VARCHAR(256) DEFAULT NULL COMMENT '源数据库路径',
    sync_mode VARCHAR(32) NOT NULL DEFAULT 'incremental' COMMENT 'incremental/full/date_range',
    date_from DATE DEFAULT NULL COMMENT '同步起始日期',
    date_to DATE DEFAULT NULL COMMENT '同步结束日期',
    source_count INT DEFAULT 0 COMMENT 'SQLite读取数',
    inserted_count INT DEFAULT 0 COMMENT '新增数',
    updated_count INT DEFAULT 0 COMMENT '更新数',
    skipped_count INT DEFAULT 0 COMMENT '跳过数(完全重复)',
    warning_count INT DEFAULT 0 COMMENT '警告数据数',
    invalid_count INT DEFAULT 0 COMMENT '无效数据数',
    failed_count INT DEFAULT 0 COMMENT '失败数',
    error_message TEXT DEFAULT NULL COMMENT '错误信息',
    sync_status VARCHAR(32) NOT NULL DEFAULT 'running' COMMENT 'running/success/partial_success/failed',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_batch (sync_batch_id),
    KEY idx_date (date_to),
    KEY idx_status (sync_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='同步任务记录表';
"""

UPSERT_PRICE = """
INSERT INTO smm_price_records
    (source, market, category, product_name, specification,
     min_price, max_price, average_price, change_value,
     unit, price_date, collected_at, source_url, collection_method,
     raw_text, extra_fields, record_hash, validation_status, validation_message,
     created_at, updated_at)
VALUES
    (%(source)s, %(market)s, %(category)s, %(product_name)s, %(specification)s,
     %(min_price)s, %(max_price)s, %(average_price)s, %(change_value)s,
     %(unit)s, %(price_date)s, %(collected_at)s, %(source_url)s, %(collection_method)s,
     %(raw_text)s, %(extra_fields)s, %(record_hash)s, %(validation_status)s, %(validation_message)s,
     %(created_at)s, %(updated_at)s)
ON DUPLICATE KEY UPDATE
    min_price = VALUES(min_price),
    max_price = VALUES(max_price),
    average_price = VALUES(average_price),
    change_value = VALUES(change_value),
    collected_at = VALUES(collected_at),
    validation_status = VALUES(validation_status),
    validation_message = VALUES(validation_message),
    updated_at = VALUES(updated_at);
"""

INSERT_QUALITY = """
INSERT INTO smm_data_quality_issues
    (record_hash, source_record_id, issue_type, issue_level, field_name,
     original_value, processed_value, issue_message, price_date, detected_at, processing_status)
VALUES
    (%(record_hash)s, %(source_record_id)s, %(issue_type)s, %(issue_level)s, %(field_name)s,
     %(original_value)s, %(processed_value)s, %(issue_message)s, %(price_date)s, %(detected_at)s, %(processing_status)s);
"""

INSERT_SYNC = """
INSERT INTO smm_sync_runs
    (sync_batch_id, sync_started_at, source_database, sync_mode, date_from, date_to, sync_status)
VALUES
    (%(sync_batch_id)s, %(sync_started_at)s, %(source_database)s, %(sync_mode)s,
     %(date_from)s, %(date_to)s, 'running');
"""

UPDATE_SYNC = """
UPDATE smm_sync_runs SET
    sync_finished_at = %(sync_finished_at)s,
    source_count = %(source_count)s,
    inserted_count = %(inserted_count)s,
    updated_count = %(updated_count)s,
    skipped_count = %(skipped_count)s,
    warning_count = %(warning_count)s,
    invalid_count = %(invalid_count)s,
    failed_count = %(failed_count)s,
    error_message = %(error_message)s,
    sync_status = %(sync_status)s
WHERE sync_batch_id = %(sync_batch_id)s;
"""


# ── 连接管理 ──────────────────────────────────────────────────

def get_connection():
    """创建 MySQL 连接（含数据库选择）。优先 mysql-connector-python，其次 pymysql。"""
    cfg = {
        "host": MYSQL_CONFIG["host"],
        "port": MYSQL_CONFIG["port"],
        "user": MYSQL_CONFIG["user"],
        "password": MYSQL_CONFIG["password"],
        "database": MYSQL_CONFIG["database"],
        "charset": MYSQL_CONFIG["charset"],
        "autocommit": False,
        "connect_timeout": MYSQL_CONFIG["connect_timeout"],
    }
    try:
        import mysql.connector
        return mysql.connector.connect(**cfg)
    except ImportError:
        pass
    try:
        import pymysql
        return pymysql.connect(**cfg)
    except ImportError:
        pass
    raise MySQLError("需要 MySQL 驱动：pip install mysql-connector-python")


def ensure_database():
    """确保目标数据库存在（首次自动创建）。"""
    if not AUTO_CREATE_DB:
        return
    cfg = {k: v for k, v in MYSQL_CONFIG.items() if k not in ("database", "charset")}
    cfg["charset"] = MYSQL_CONFIG["charset"]
    cfg["autocommit"] = True
    try:
        import mysql.connector
        conn = mysql.connector.connect(**cfg)
    except ImportError:
        import pymysql
        conn = pymysql.connect(**cfg)
    cursor = conn.cursor()
    db_name = MYSQL_CONFIG["database"]
    cursor.execute(
        f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
        f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    cursor.close()
    conn.close()
    log.info("MySQL 数据库 %s 已就绪", db_name)


def ensure_tables(conn):
    """确保所有需要的表存在。"""
    cursor = conn.cursor()
    cursor.execute(CREATE_PRICE_TABLE)
    cursor.execute(CREATE_QUALITY_TABLE)
    cursor.execute(CREATE_SYNC_TABLE)
    conn.commit()
    cursor.close()
    log.info("MySQL 表结构已就绪")


# ── 批量写入 ──────────────────────────────────────────────────

def _row_to_params(row: dict) -> dict:
    """将 SQLite 行 dict 转为 MySQL UPSERT 参数。"""
    def _dec(v):
        if v is None: return None
        try: return Decimal(str(v))
        except Exception: return None
    def _dt(v):
        if v is None: return None
        s = str(v).strip()
        return s if s else None

    return {
        "source": row.get("source", "") or "",
        "market": row.get("market", "") or "",
        "category": row.get("category", "") or "",
        "product_name": row.get("product_name", "") or "",
        "specification": (row.get("specification") or ""),
        "min_price": _dec(row.get("min_price")),
        "max_price": _dec(row.get("max_price")),
        "average_price": _dec(row.get("average_price")),
        "change_value": _dec(row.get("change_value")),
        "unit": row.get("unit", "") or "",
        "price_date": _dt(row.get("price_date")),
        "collected_at": _dt(row.get("collected_at")),
        "source_url": row.get("source_url"),
        "collection_method": row.get("collection_method"),
        "raw_text": row.get("raw_text"),
        "extra_fields": row.get("extra_fields"),
        "record_hash": row.get("record_hash", "") or "",
        "validation_status": row.get("validation_status"),
        "validation_message": row.get("validation_message"),
        "created_at": _dt(row.get("created_at")),
        "updated_at": _dt(row.get("updated_at")),
    }


def batch_upsert_prices(conn, rows: list[dict]) -> dict:
    """批量 upsert 价格数据，返回统计 dict。

    使用 record_hash 作为唯一键。每行在执行前先 SELECT 判断是 INSERT 还是 UPDATE。
    """
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
    if not rows:
        return stats

    cursor = conn.cursor()
    batch = []
    for row in rows:
        try:
            params = _row_to_params(row)
            rh = params["record_hash"]
            if not rh:
                stats["failed"] += 1
                continue
            # 检查是否存在
            cursor.execute(
                "SELECT id, record_hash FROM smm_price_records WHERE record_hash = %s", (rh,))
            existing = cursor.fetchone()
            if existing:
                if existing[1] == rh:
                    stats["skipped"] += 1
                    continue
                else:
                    stats["updated"] += 1
            else:
                stats["inserted"] += 1
            batch.append(params)

            if len(batch) >= SYNC_BATCH_SIZE:
                _flush_batch(cursor, batch)
                conn.commit()
                batch.clear()
        except Exception:
            stats["failed"] += 1
            log.exception("处理记录失败: %s", row.get("product_name", "?"))

    if batch:
        _flush_batch(cursor, batch)
        conn.commit()

    cursor.close()
    return stats


def _flush_batch(cursor, batch: list[dict]):
    """执行一批 upsert。"""
    for params in batch:
        cursor.execute(UPSERT_PRICE, params)


def insert_quality_issues(conn, issues: list[dict]):
    """批量写入数据质量问题。"""
    if not issues:
        return
    cursor = conn.cursor()
    for iss in issues:
        cursor.execute(INSERT_QUALITY, {
            "record_hash": iss.get("record_hash", ""),
            "source_record_id": iss.get("source_record_id"),
            "issue_type": iss.get("issue_type", "unknown"),
            "issue_level": iss.get("issue_level", "warning"),
            "field_name": iss.get("field_name"),
            "original_value": str(iss.get("original_value", ""))[:1000] if iss.get("original_value") else None,
            "processed_value": str(iss.get("processed_value", ""))[:1000] if iss.get("processed_value") else None,
            "issue_message": iss.get("issue_message", ""),
            "price_date": iss.get("price_date"),
            "detected_at": iss.get("detected_at", datetime.now().isoformat(timespec="seconds")),
            "processing_status": iss.get("processing_status", "pending"),
        })
    conn.commit()
    cursor.close()


def create_sync_run(conn, batch_id: str, source_db: str, mode: str,
                    date_from=None, date_to=None):
    """创建一条同步运行记录，状态为 running。"""
    cursor = conn.cursor()
    cursor.execute(INSERT_SYNC, {
        "sync_batch_id": batch_id,
        "sync_started_at": datetime.now().isoformat(timespec="seconds"),
        "source_database": source_db,
        "sync_mode": mode,
        "date_from": date_from,
        "date_to": date_to,
    })
    conn.commit()
    cursor.close()
    log.info("同步批次 %s 已创建", batch_id)


def update_sync_run(conn, batch_id: str, stats: dict, status: str, error_msg: str = ""):
    """更新同步运行记录。"""
    cursor = conn.cursor()
    cursor.execute(UPDATE_SYNC, {
        "sync_batch_id": batch_id,
        "sync_finished_at": datetime.now().isoformat(timespec="seconds"),
        "source_count": stats.get("source_count", 0),
        "inserted_count": stats.get("inserted", 0),
        "updated_count": stats.get("updated", 0),
        "skipped_count": stats.get("skipped", 0),
        "warning_count": stats.get("warning", 0),
        "invalid_count": stats.get("invalid", 0),
        "failed_count": stats.get("failed", 0),
        "error_message": error_msg[:2000] if error_msg else "",
        "sync_status": status,
    })
    conn.commit()
    cursor.close()
    log.info("同步批次 %s 状态更新为 %s", batch_id, status)


def check_synced_date(conn, target_date: str) -> bool:
    """检查指定日期是否已有成功同步记录。"""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM smm_sync_runs WHERE date_to = %s AND sync_status = 'success'",
        (target_date,))
    count = cursor.fetchone()[0]
    cursor.close()
    return count > 0
