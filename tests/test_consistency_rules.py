"""数据真实性规则测试：涨跌榜过滤/排序/互斥、7日趋势不填充、指标与 DB 对账、校验器只读性。

合成数据集（需求 #34）：
  A +5%  B +2%  C 0%  D -1%  E -3%  F -8%
正确：涨幅榜 [A,B]；跌幅榜 [F,E,D]；C 两榜都不进。
"""
from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "file_server.py"
VSCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_data_consistency.py"

PORTAL_YAML = """
portal:
  title: "锂电价格与回收业务数据中心"
  subtitle: "test"
metrics:
  - {key: A, name: "产品A", category: "X", product: "A"}
  - {key: B, name: "产品B", category: "X", product: "B"}
  - {key: C, name: "产品C", category: "X", product: "C"}
  - {key: D, name: "产品D", category: "X", product: "D"}
  - {key: E, name: "产品E", category: "X", product: "E"}
  - {key: F, name: "产品F", category: "X", product: "F"}
  - {key: G, name: "产品G", category: "X", product: "G"}
  - {key: H, name: "产品H", category: "X", product: "H"}
auth:
  session_ttl_hours: 10
  admin_session_ttl_hours: 4
  lockout_max_failures: 5
  lockout_window_minutes: 10
  lockout_minutes: 10
  password_min_length: 8
health: {cache_seconds: 30}
admin:
  log_names: {cron: "logs/cron.log"}
  max_log_lines: 200
  log_days_back: 7
tasks: []
"""

# A~F：08-14(prev) → 08-17(cur)；G：仅 07-01（陈旧 >14 天）；H：仅 08-17（单点）
BASE_ROWS = [
    # (product, price_date, avg, change_value)
    ("A", "2026-08-14", 100, 0), ("A", "2026-08-17", 105, 5),
    ("B", "2026-08-14", 100, 0), ("B", "2026-08-17", 102, 2),
    ("C", "2026-08-14", 100, 0), ("C", "2026-08-17", 100, 0),
    ("D", "2026-08-14", 100, 0), ("D", "2026-08-17", 99, -1),
    ("E", "2026-08-14", 100, 0), ("E", "2026-08-17", 97, -3),
    ("F", "2026-08-14", 100, 0), ("F", "2026-08-17", 92, -8),
    ("G", "2026-07-01", 50, 0),
    ("H", "2026-08-17", 77, 0),
]

SCHEMA = """
CREATE TABLE lithium_spot_prices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL DEFAULT 'SMM',
  market TEXT NOT NULL DEFAULT 'SMM锂电现货',
  category TEXT NOT NULL,
  product_name TEXT NOT NULL,
  specification TEXT NOT NULL DEFAULT '',
  min_price TEXT, max_price TEXT, average_price TEXT, change_value TEXT,
  unit TEXT NOT NULL DEFAULT '元/吨',
  price_date TEXT NOT NULL,
  collected_at TEXT NOT NULL,
  source_url TEXT, collection_method TEXT, raw_text TEXT, extra_fields TEXT,
  record_hash TEXT NOT NULL DEFAULT '',
  validation_status TEXT, validation_message TEXT,
  created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT ''
);
"""


def _load_module():
    spec = importlib.util.spec_from_file_location("file_server_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_verifier():
    spec = importlib.util.spec_from_file_location("verify_data_consistency_under_test", VSCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_db(db_path: Path, rows=BASE_ROWS) -> None:
    with sqlite3.connect(db_path) as con:
        con.execute(SCHEMA)
        for (prod, date, avg, cv) in rows:
            con.execute(
                "INSERT INTO lithium_spot_prices(category, product_name, specification,"
                " average_price, change_value, unit, price_date, collected_at,"
                " validation_status) VALUES('X',?,'S',?,?,'元/吨',?,'%s','valid')"
                % ("2026-08-17 10:00:00" if date == "2026-08-17" else f"{date} 10:00:00"),
                (prod, avg, cv, date))


def _write_daily_csv(exports_dir: Path, date: str, rows: list[tuple[str, str]]) -> None:
    p = exports_dir / date[:4] / date[5:7] / "每日汇总" / "CSV" / f"SMM锂电现货价格_{date}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write("source,market,category,product_name,specification,min_price,max_price,"
                "average_price,change_value,unit,price_date,collected_at\n")
        for (prod, avg) in rows:
            f.write(f"SMM,SMM锂电现货,X,{prod},S,,,{avg},,元/吨,{date},2026-08-17 10:00:00\n")


@pytest.fixture
def fs_env(tmp_path, monkeypatch):
    """加载 file_server 模块并把路径指向 tmp（不起 HTTP 服务）。"""
    fs = _load_module()
    monkeypatch.setattr(fs, "DB_PATH", tmp_path / "smm.db")
    monkeypatch.setattr(fs, "EXPORTS_ROOT", tmp_path / "exports")
    (tmp_path / "exports").mkdir()
    portal_cfg = tmp_path / "portal.yaml"
    portal_cfg.write_text(PORTAL_YAML, encoding="utf-8")
    monkeypatch.setattr(fs, "PORTAL_CFG", portal_cfg)
    fs._portal_cfg_cache.update({"mtime": 0.0, "data": {}})
    _build_db(tmp_path / "smm.db")
    return fs, tmp_path


# ── 涨跌榜规则（需求 #15/16/17/18/34） ─────────────────

class TestRankingRules:
    def test_gainers_only_positive_desc(self, fs_env):
        fs, _ = fs_env
        r = fs._rankings(as_of="2026-08-18")
        assert [c["product"] for c in r["top_gainers"]] == ["A", "B"]
        assert all(c["pct"] > 0 for c in r["top_gainers"])
        assert [c["pct"] for c in r["top_gainers"]] == sorted(
            [c["pct"] for c in r["top_gainers"]], reverse=True)

    def test_losers_only_negative_asc(self, fs_env):
        fs, _ = fs_env
        r = fs._rankings(as_of="2026-08-18")
        assert [c["product"] for c in r["top_losers"]] == ["F", "E", "D"]
        assert all(c["pct"] < 0 for c in r["top_losers"])
        assert [c["pct"] for c in r["top_losers"]] == sorted(
            [c["pct"] for c in r["top_losers"]])

    def test_no_positive_in_losers(self, fs_env):
        fs, _ = fs_env
        assert all(c["pct"] < 0 for c in fs._rankings(as_of="2026-08-18")["top_losers"])

    def test_no_negative_in_gainers(self, fs_env):
        fs, _ = fs_env
        assert all(c["pct"] > 0 for c in fs._rankings(as_of="2026-08-18")["top_gainers"])

    def test_zero_excluded_from_both(self, fs_env):
        fs, _ = fs_env
        r = fs._rankings(as_of="2026-08-18")
        products = [c["product"] for c in r["top_gainers"] + r["top_losers"]]
        assert "C" not in products

    def test_fewer_than_five_not_padded(self, fs_env):
        fs, _ = fs_env
        r = fs._rankings(as_of="2026-08-18")
        assert len(r["top_gainers"]) == 2   # 只有 2 个上涨，不凑满 5
        assert len(r["top_losers"]) == 3    # 只有 3 个下跌，不凑满 5

    def test_based_on_window_dates(self, fs_env):
        fs, _ = fs_env
        r = fs._rankings(as_of="2026-08-18")
        assert r["based_on"] == {"date": "2026-08-17", "prev_date": "2026-08-14"}

    def test_today_excluded_next_day_mode(self, fs_env):
        # 次日采集模式：as_of=当天(8-17)时，8-17 数据视为未发布 → 窗口应为 8-14 vs 8-13
        fs, tmp = fs_env
        with sqlite3.connect(tmp / "smm.db") as con:
            for (prod, avg) in [("A", 101), ("B", 98), ("C", 100), ("D", 100),
                                ("E", 100), ("F", 105)]:
                con.execute(
                    "INSERT INTO lithium_spot_prices(category, product_name, specification,"
                    " average_price, change_value, unit, price_date, collected_at,"
                    " validation_status) VALUES('X',?,'S',?,0,'元/吨','2026-08-13',"
                    "'2026-08-17 10:00:00','valid')", (prod, avg))
        r = fs._rankings(as_of="2026-08-17")
        assert r["based_on"] == {"date": "2026-08-14", "prev_date": "2026-08-13"}
        # 8-13 → 8-14：A 101→100 跌 0.99%；F 105→100 跌 4.76%；B 98→100 涨 2.04%
        assert [c["product"] for c in r["top_losers"]] == ["F", "A"]
        assert [c["product"] for c in r["top_gainers"]] == ["B"]
        assert all(c["date"] == "2026-08-14" and c["prev_date"] == "2026-08-13"
                   for c in r["top_gainers"] + r["top_losers"])

    def test_no_dual_listing(self, fs_env):
        fs, _ = fs_env
        r = fs._rankings(as_of="2026-08-18")
        gainers = {c["product"] for c in r["top_gainers"]}
        losers = {c["product"] for c in r["top_losers"]}
        assert gainers.isdisjoint(losers)   # 同一产品不会同时上榜


# ── 指标卡与趋势（需求 #10/11/7/8/9） ──────────────────

class TestMetricRules:
    def test_latest_price(self, fs_env):
        fs, _ = fs_env
        m = {x["product"]: x for x in fs._metrics(fs._db_latest_date())}
        assert m["A"]["value"] == 105.0 and m["A"]["price_date"] == "2026-08-17"
        assert m["F"]["value"] == 92.0

    def test_change_pct_formula(self, fs_env):
        fs, _ = fs_env
        m = {x["product"]: x for x in fs._metrics(fs._db_latest_date())}
        assert m["A"]["change_pct"] == 5.0 and m["A"]["change"] == 5.0
        assert m["F"]["change_pct"] == -8.0
        assert m["D"]["change_pct"] == -1.0
        assert m["A"]["prev_date"] == "2026-08-14"

    def test_7day_history_no_padding(self, fs_env):
        fs, tmp = fs_env
        # 给 A 追加 4 个真实日期：8-10~8-13 → A 共 6 个日期 → spark_points 恰好 6 个点（不填充到 7）
        with sqlite3.connect(tmp / "smm.db") as con:
            for (d, v) in [("2026-08-10", 96), ("2026-08-11", 97),
                           ("2026-08-12", 98), ("2026-08-13", 99)]:
                con.execute(
                    "INSERT INTO lithium_spot_prices(category, product_name, specification,"
                    " average_price, change_value, unit, price_date, collected_at,"
                    " validation_status) VALUES('X','A','S',?,0,'元/吨',?,"
                    "'2026-08-17 10:00:00','valid')", (v, d))
        m = {x["product"]: x for x in fs._metrics(fs._db_latest_date())}
        pts = m["A"]["spark_points"]
        # 真实日期数 6 < 7 → 只画 6 个真实点，绝不补齐 7
        assert len(pts) == 6
        assert [p["date"] for p in pts] == ["2026-08-10", "2026-08-11", "2026-08-12",
                                            "2026-08-13", "2026-08-14", "2026-08-17"]

    def test_spark_points_carry_dates(self, fs_env):
        fs, _ = fs_env
        m = {x["product"]: x for x in fs._metrics(fs._db_latest_date())}
        pts = m["A"]["spark_points"]
        assert all(set(p) == {"date", "value"} for p in pts)
        assert pts[-1] == {"date": "2026-08-17", "value": 105.0}

    def test_single_point_no_change(self, fs_env):
        fs, _ = fs_env
        m = {x["product"]: x for x in fs._metrics(fs._db_latest_date())}
        h = m["H"]
        assert h["change_pct"] is None and h["change"] is None
        assert h["prev_date"] is None and len(h["spark_points"]) == 1

    def test_stale_flag(self, fs_env):
        fs, _ = fs_env
        m = {x["product"]: x for x in fs._metrics(fs._db_latest_date())}
        assert m["G"]["is_stale"] is True          # 07-01 vs 全局最新 08-17 >14 天
        assert m["A"]["is_stale"] is False         # 8-17 当日
        assert m["H"]["is_stale"] is False


# ── 校验器（只读、退出码、抓 bug） ─────────────────────

class TestVerifier:
    def _run(self, tmp, db="smm.db", with_exports=True, portal="portal.yaml"):
        exports = tmp / "exports"
        if with_exports:
            for date, rows in (("2026-08-14", [("A", 100), ("B", 100), ("C", 100),
                                               ("D", 100), ("E", 100), ("F", 100)]),
                               ("2026-08-17", [("A", 105), ("B", 102), ("C", 100),
                                               ("D", 99), ("E", 97), ("F", 92), ("H", 77)]),
                               ("2026-07-01", [("G", 50)])):
                _write_daily_csv(exports, date, rows)
        return subprocess.run(
            [sys.executable, str(VSCRIPT), "--db", str(tmp / db),
             "--exports-dir", str(exports), "--portal-config", str(tmp / portal),
             "--as-of", "2026-08-18"],
            capture_output=True, text=True, timeout=60)

    def test_verifier_pass(self, fs_env):
        _, tmp = fs_env
        # 仅 A-F（无单点产品）→ 无 WARNING → 退出码 0
        af_yaml = PORTAL_YAML.replace("  - {key: G, name: \"产品G\", category: \"X\", product: \"G\"}\n", "") \
                            .replace("  - {key: H, name: \"产品H\", category: \"X\", product: \"H\"}\n", "")
        (tmp / "portal_af.yaml").write_text(af_yaml, encoding="utf-8")
        r = self._run(tmp, portal="portal_af.yaml")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "[PASS]" in r.stdout and "[ERROR]" not in r.stdout

    def test_verifier_catches_losers_bug(self, fs_env, monkeypatch):
        # 直接单测 check_rankings 的规则防线：伪造一个把正涨跌塞进跌幅榜的 _rankings
        fs, tmp = fs_env
        v = _load_verifier()
        monkeypatch.setattr(v, "fs", fs)   # 让校验器使用合成 DB 的 fs 实例
        fake = {
            "top_gainers": [],
            "top_losers": [{"product": "X", "pct": 0.84, "specification": "",
                            "value": 1, "prev_date": "2026-08-14", "date": "2026-08-17"}],
            "based_on": {"date": "2026-08-17", "prev_date": "2026-08-14"},
        }
        monkeypatch.setattr(fs, "_rankings", lambda as_of=None: fake)
        rep = v.Reporter("synthetic")
        with sqlite3.connect(f"file:{tmp / 'smm.db'}?mode=ro", uri=True) as con:
            v.check_rankings(rep, con, "2026-08-18")
        assert rep.exit_code() == 1
        assert any(s == "ERROR" and "跌幅榜" in msg for s, msg in rep.items)

    def test_verifier_readonly(self, fs_env):
        _, tmp = fs_env
        db = tmp / "smm.db"
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        self._run(tmp)
        after = hashlib.sha256(db.read_bytes()).hexdigest()
        assert before == after   # 校验器绝不修改数据库

    def test_verifier_flags_export_mismatch(self, fs_env):
        # 导出文件与 DB 不一致 → ERROR + 退出码 1
        _, tmp = fs_env
        _write_daily_csv(tmp / "exports", "2026-08-17",
                         [("A", 999), ("B", 102), ("C", 100), ("D", 99), ("E", 97), ("F", 92)])
        _write_daily_csv(tmp / "exports", "2026-08-14",
                         [("A", 100), ("B", 100), ("C", 100), ("D", 100), ("E", 100), ("F", 100)])
        r = self._run(tmp, with_exports=False)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "导出一致性" in r.stdout and "[ERROR]" in r.stdout


# ── 导出与 DB 一致性（需求 #26） ────────────────────────

class TestExportConsistency:
    def test_daily_csv_matches_db(self, fs_env):
        fs, tmp = fs_env
        exports = tmp / "exports"
        _write_daily_csv(exports, "2026-08-17",
                         [("A", 105), ("B", 102), ("C", 100), ("D", 99), ("E", 97),
                          ("F", 92), ("H", 77)])
        # 校验器检查6 直接跑通（仅 8-14/7-01 无导出 → 历史缺口 WARNING → 退出码 2）
        r = subprocess.run(
            [sys.executable, str(VSCRIPT), "--db", str(tmp / "smm.db"),
             "--exports-dir", str(exports), "--portal-config", str(tmp / "portal.yaml"),
             "--as-of", "2026-08-18"],
            capture_output=True, text=True, timeout=60)
        assert r.returncode == 2 and "[ERROR]" not in r.stdout
        assert "导出一致性" in r.stdout and "[PASS]" in r.stdout
