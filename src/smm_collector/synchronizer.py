"""SQLite → MySQL 同步编排。

负责：读取 SQLite、数据标准化、异常检测、批量写入 MySQL、质量报告、重试。
"""
from __future__ import annotations
import hashlib, logging, sqlite3, time, uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .cleaner import normalize_business_key, normalize_str, normalize_unit, parse_decimal
from .validator import validate_row, check_price_volatility
from .mysql_database import (
    get_connection, ensure_database, ensure_tables,
    batch_upsert_prices, insert_quality_issues,
    create_sync_run, update_sync_run, check_synced_date,
    SYNC_BATCH_SIZE, SYNC_MAX_RETRIES, SYNC_RETRY_INTERVAL, MySQLError,
)

log = logging.getLogger("smm_collector.sync")


def compute_record_hash(row: dict) -> str:
    """基于标准化后的业务字段计算 SHA-256 哈希。"""
    key_fields = ["source", "market", "category", "product_name", "specification", "unit", "price_date"]
    parts = []
    for k in key_fields:
        v = row.get(k)
        parts.append(normalize_str(str(v)) if v else "")
    data = "\x1f".join(parts)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _classify_rows(rows: list[dict]) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """将行按 validation_status 分类为 valid / warning / invalid，并收集异常问题。

    返回 (valid_rows, warning_rows, invalid_rows, quality_issues)。
    """
    valid_rows, warning_rows, invalid_rows = [], [], []
    quality_issues = []

    for r in rows:
        r = normalize_business_key(r)
        status = r.get("validation_status", "valid")
        if status == "invalid":
            invalid_rows.append(r)
            quality_issues.append({
                "record_hash": r.get("record_hash", ""),
                "issue_type": "validation_failed",
                "issue_level": "error",
                "field_name": None,
                "original_value": None,
                "processed_value": None,
                "issue_message": r.get("validation_message", "校验失败"),
                "price_date": r.get("price_date"),
                "detected_at": datetime.now().isoformat(timespec="seconds"),
                "processing_status": "pending",
            })
        elif status == "warning":
            warning_rows.append(r)
            quality_issues.append({
                "record_hash": r.get("record_hash", ""),
                "issue_type": "validation_warning",
                "issue_level": "warning",
                "field_name": None,
                "original_value": None,
                "processed_value": None,
                "issue_message": r.get("validation_message", "校验警告"),
                "price_date": r.get("price_date"),
                "detected_at": datetime.now().isoformat(timespec="seconds"),
                "processing_status": "pending",
            })
        else:
            valid_rows.append(r)

    return valid_rows, warning_rows, invalid_rows, quality_issues


def load_from_sqlite(db_path: Path, date_from=None, date_to=None) -> list[dict]:
    """从 SQLite 读取数据，返回 dict 列表。"""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    if date_from and date_to:
        rows = con.execute(
            "SELECT * FROM lithium_spot_prices WHERE price_date BETWEEN ? AND ? ORDER BY price_date, id",
            (str(date_from), str(date_to))).fetchall()
    elif date_from:
        rows = con.execute(
            "SELECT * FROM lithium_spot_prices WHERE price_date >= ? ORDER BY price_date, id",
            (str(date_from),)).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM lithium_spot_prices ORDER BY price_date, id").fetchall()

    con.close()
    return [dict(r) for r in rows]


def sync(sqlite_path: Path, date_from=None, date_to=None, dry_run: bool = False) -> dict:
    """执行一次 SQLite → MySQL 同步。

    返回统计 dict 供日志和报告使用。
    """
    started = datetime.now()
    batch_id = started.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    mode = "date_range" if (date_from and date_to) else "incremental" if date_from else "full"

    stats = {
        "batch_id": batch_id,
        "sync_mode": mode,
        "date_from": str(date_from) if date_from else None,
        "date_to": str(date_to) if date_to else None,
        "source_count": 0,
        "inserted": 0, "updated": 0, "skipped": 0, "failed": 0,
        "warning": 0, "invalid": 0,
        "quality_issues": 0,
        "status": "running",
        "error": "",
    }

    # 1) 从 SQLite 读取
    all_rows = load_from_sqlite(sqlite_path, date_from, date_to)
    stats["source_count"] = len(all_rows)
    if not all_rows:
        stats["status"] = "success"
        log.info("SQLite 无数据需要同步")
        return stats

    # 2) 分类：valid / warning / invalid
    valid_rows, warning_rows, invalid_rows, quality_issues = _classify_rows(all_rows)
    stats["warning"] = len(warning_rows)
    stats["invalid"] = len(invalid_rows)
    stats["quality_issues"] = len(quality_issues)

    # 所有要写入主表的数据（valid + warning）
    to_sync = valid_rows + warning_rows

    if dry_run:
        stats["inserted"] = len(to_sync)
        stats["status"] = "success"
        return stats

    # 3) MySQL 写入（带重试）
    conn = None
    last_error = ""
    for attempt in range(SYNC_MAX_RETRIES):
        try:
            ensure_database()
            conn = get_connection()
            ensure_tables(conn)

            # 检查是否已同步（仅 incremental 模式）
            if mode == "incremental" and date_from and not date_to:
                if check_synced_date(conn, str(date_from)):
                    log.info("日期 %s 已有成功同步记录，跳过", date_from)
                    stats["status"] = "success"
                    return stats

            # 创建同步记录
            create_sync_run(conn, batch_id, str(sqlite_path), mode,
                            stats["date_from"], stats["date_to"])

            # 批量写入价格
            price_stats = batch_upsert_prices(conn, to_sync)
            stats.update(price_stats)

            # 写入质量问题
            insert_quality_issues(conn, quality_issues)

            # 更新同步状态
            status = "success" if stats["failed"] == 0 else "partial_success"
            update_sync_run(conn, batch_id, stats, status)
            conn.close()
            stats["status"] = status
            log.info("同步完成 batch=%s inserted=%d updated=%d skipped=%d failed=%d",
                     batch_id, stats["inserted"], stats["updated"],
                     stats["skipped"], stats["failed"])
            return stats

        except Exception as e:
            last_error = str(e)
            log.error("同步尝试 %d/%d 失败: %s", attempt + 1, SYNC_MAX_RETRIES, last_error)
            if conn:
                try:
                    update_sync_run(conn, batch_id, stats, "failed", last_error)
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
            if attempt < SYNC_MAX_RETRIES - 1:
                time.sleep(SYNC_RETRY_INTERVAL)

    stats["status"] = "failed"
    stats["error"] = last_error
    return stats


def generate_quality_report(stats: dict, output_dir: Path | None = None) -> dict:
    """从同步统计数据生成数据质量报告 dict。"""
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "batch_id": stats.get("batch_id", ""),
        "sync_mode": stats.get("sync_mode", ""),
        "date_range": f"{stats.get('date_from','')} ~ {stats.get('date_to','')}",
        "total_source": stats.get("source_count", 0),
        "valid_count": stats.get("source_count", 0) - stats.get("warning", 0) - stats.get("invalid", 0),
        "warning_count": stats.get("warning", 0),
        "invalid_count": stats.get("invalid", 0),
        "mysql_inserted": stats.get("inserted", 0),
        "mysql_updated": stats.get("updated", 0),
        "mysql_skipped": stats.get("skipped", 0),
        "mysql_failed": stats.get("failed", 0),
        "quality_issues": stats.get("quality_issues", 0),
        "sync_status": stats.get("status", "unknown"),
    }
