from __future__ import annotations
import json, sqlite3, time
from datetime import datetime

SCHEMA="""
CREATE TABLE IF NOT EXISTS lithium_spot_prices (
 id INTEGER PRIMARY KEY, source TEXT NOT NULL, market TEXT NOT NULL, category TEXT NOT NULL,
 product_name TEXT NOT NULL, specification TEXT NOT NULL DEFAULT '', min_price TEXT, max_price TEXT,
 average_price TEXT, change_value TEXT, unit TEXT NOT NULL DEFAULT '', price_date TEXT NOT NULL,
 collected_at TEXT NOT NULL, source_url TEXT, collection_method TEXT, raw_text TEXT, extra_fields TEXT,
 record_hash TEXT NOT NULL, validation_status TEXT, validation_message TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(source,market,category,product_name,specification,unit,price_date));
CREATE TABLE IF NOT EXISTS collection_runs (
 run_id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, target_date TEXT, status TEXT,
 expected_categories TEXT, success_categories TEXT, failed_categories TEXT,
 total_raw_rows INTEGER, total_clean_rows INTEGER, error_message TEXT);
"""

class Database:
	def __init__(self, path):
		self.path = path

	def connect(self):
		con = sqlite3.connect(self.path, timeout=30)
		con.executescript(SCHEMA)
		con.row_factory = sqlite3.Row
		return con

	def upsert(self, rows, retries=3):
		stats = {"inserted": 0, "updated": 0, "duplicate": 0}
		for attempt in range(retries):
			try:
				with self.connect() as con:
					for row in rows:
						key = (row["source"], row["market"], row["category"],
						       row["product_name"], row.get("specification") or "",
						       row.get("unit") or "", str(row["price_date"]))
						old = con.execute(
							"SELECT id,record_hash FROM lithium_spot_prices "
							"WHERE source=? AND market=? AND category=? AND product_name=? "
							"AND specification=? AND unit=? AND price_date=?", key).fetchone()
						now = datetime.now().isoformat(timespec="seconds")
						vals = [self._decimal_text(row.get(k)) for k in
						        ("min_price", "max_price", "average_price", "change_value")]
						if not old:
							con.execute(
								"INSERT INTO lithium_spot_prices(source,market,category,"
								"product_name,specification,min_price,max_price,average_price,"
								"change_value,unit,price_date,collected_at,source_url,"
								"collection_method,raw_text,extra_fields,record_hash,"
								"validation_status,validation_message,created_at,updated_at) "
								"VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
								key[:5] + tuple(vals) + (key[5], key[6],
									str(row["collected_at"]), row.get("source_url"),
									row.get("collection_method"), row.get("raw_text"),
									row.get("extra_fields"), row["record_hash"],
									row.get("validation_status"), row.get("validation_message"),
									now, now))
							stats["inserted"] += 1
						elif old[1] == row["record_hash"]:
							stats["duplicate"] += 1
						else:
							con.execute(
								"UPDATE lithium_spot_prices SET min_price=?,max_price=?,"
								"average_price=?,change_value=?,collected_at=?,source_url=?,"
								"collection_method=?,raw_text=?,extra_fields=?,record_hash=?,"
								"validation_status=?,validation_message=?,updated_at=? WHERE id=?",
								tuple(vals) + (str(row["collected_at"]), row.get("source_url"),
									row.get("collection_method"), row.get("raw_text"),
									row.get("extra_fields"), row["record_hash"],
									row.get("validation_status"), row.get("validation_message"),
									now, old[0]))
							stats["updated"] += 1
				return stats
			except sqlite3.OperationalError as e:
				if "locked" not in str(e).lower() or attempt == retries - 1:
					raise
				time.sleep(2 ** attempt)
		return stats

	def save_run(self, meta):
		with self.connect() as con:
			con.execute(
				"INSERT OR REPLACE INTO collection_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
				(meta["run_id"], meta["started_at"], meta.get("finished_at"),
				 meta.get("target_date"), meta.get("status"),
				 json.dumps(meta.get("expected_categories", []), ensure_ascii=False),
				 json.dumps(meta.get("success_categories", []), ensure_ascii=False),
				 json.dumps(meta.get("failed_categories", []), ensure_ascii=False),
				 meta.get("total_raw_rows", 0), meta.get("total_clean_rows", 0),
				 meta.get("error_message", "")))

	# ── 多日查询接口 ──────────────────────────────────────────

	def get_latest_price_dates(self, limit: int = 3) -> list[str]:
		"""获取 SQLite 中最近 N 个不同的 price_date（降序）。"""
		with self.connect() as con:
			rows = con.execute(
				"SELECT DISTINCT price_date FROM lithium_spot_prices "
				"ORDER BY price_date DESC LIMIT ?", (limit,)).fetchall()
		return [r[0] for r in rows]

	def get_distinct_price_date_count(self) -> int:
		"""获取 SQLite 中不同 price_date 的总数。"""
		with self.connect() as con:
			return con.execute(
				"SELECT COUNT(DISTINCT price_date) FROM lithium_spot_prices").fetchone()[0]

	def get_records_by_price_dates(self, dates: list[str], exclude_invalid: bool = True) -> list[dict]:
		"""批量查询指定价格日期的数据，返回 dict 列表。

		exclude_invalid=True 时排除 validation_status='invalid' 的记录。
		"""
		if not dates:
			return []
		placeholders = ",".join("?" for _ in dates)
		with self.connect() as con:
			if exclude_invalid:
				rows = con.execute(
					f"SELECT * FROM lithium_spot_prices "
					f"WHERE price_date IN ({placeholders}) AND validation_status != 'invalid' "
					f"ORDER BY category, product_name, specification, unit, price_date",
					dates).fetchall()
			else:
				rows = con.execute(
					f"SELECT * FROM lithium_spot_prices "
					f"WHERE price_date IN ({placeholders}) "
					f"ORDER BY category, product_name, specification, unit, price_date",
					dates).fetchall()
		return [dict(r) for r in rows]

	def get_all_records(self) -> list[dict]:
		"""获取全部数据（用于测试和小数据量场景）。"""
		with self.connect() as con:
			rows = con.execute(
				"SELECT * FROM lithium_spot_prices ORDER BY category, product_name, price_date"
			).fetchall()
		return [dict(r) for r in rows]

	@staticmethod
	def _decimal_text(value):
		from decimal import Decimal
		if value is None:
			return None
		return format(value, "f") if isinstance(value, Decimal) else str(value)
