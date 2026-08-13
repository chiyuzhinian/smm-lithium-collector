"""ops_monitor 测试：故障模拟（fixture 覆盖输入）→ OK/WARNING/CRITICAL 映射。

不读取真实数据：manifest / validation JSON / collection_runs 全部用合成 fixture，
/proc 与 statvfs 用 monkeypatch 注入。
"""
from __future__ import annotations

import io
import json
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from smm_collector import ops_monitor

HEALTH_CFG = {"disk_warn_pct": 80, "disk_crit_pct": 90, "mem_warn_pct": 80,
              "mem_crit_pct": 90, "load_warn": 3.0, "load_crit": 4.0,
              "collector_timeout_minutes": 45, "freshness_check_hour": 14,
              "cache_seconds": 30}


def _write_manifest(root, date_str, status="success", expected=40, success=40,
                    failed=0, missing=None, decision="updated_formal",
                    formal_updated_at="2026-08-13T12:32:40", runs=None):
    p = root / "data" / "exports" / date_str[:4] / date_str[5:7] / "每日汇总" / f"SMM数据状态_{date_str}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    man = {
        "generated_at": "2026-08-13T12:32:40", "target_date": date_str,
        "collection": {"status": status, "categories_expected": expected,
                       "categories_succeeded": success, "categories_failed": failed,
                       "failed_category_names": [], "missing_categories": missing or [],
                       "valid_count": 300, "warning_count": 2, "invalid_count": 0,
                       "date_alignment_ratio": 0.74},
        "fixed_summary": {"decision": decision, "eligible": decision == "updated_formal",
                          "formal_updated_at": formal_updated_at,
                          "formal_status": "完整", "reasons": [],
                          "temp_snapshot_path": None},
        "runs": runs or [],
    }
    p.write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    return man


def _write_validation(root, date_str, verdict, error=0, warning=0, info=10):
    p = root / "logs" / "validation" / f"validation_{date_str}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    v = {"generated_at": "2026-08-13T12:33:00", "date": date_str, "verdict": verdict,
         "layers": {"file": {"status": "ok", "xlsx_data_rows": 100, "csv_data_rows": 100}},
         "issues": [], "counts": {"error": error, "warning": warning, "info": info}}
    p.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    return v


def _fake_db(rows_by_keyword: dict):
    def q(db_path, sql, params=()):
        for key, rows in rows_by_keyword.items():
            if key in sql:
                return rows
        return []
    return q


RUN_OK = {"started_at": "2026-08-13T10:00:01", "finished_at": "2026-08-13T10:02:02",
          "target_date": "2026-08-13", "status": "success"}


# ── 期望业务日期 ──────────────────────────────────────

class TestExpectedBusinessDate:
    @pytest.mark.parametrize("now,expected", [
        (datetime(2026, 8, 13, 15, 0), "2026-08-13"),   # 周四 15:00 → 当日
        (datetime(2026, 8, 13, 9, 0), "2026-08-12"),    # 周四 9:00（未到检查时点）→ 周三
        (datetime(2026, 8, 15, 15, 0), "2026-08-14"),   # 周六 → 周五
        (datetime(2026, 8, 16, 15, 0), "2026-08-14"),   # 周日 → 周五
        (datetime(2026, 8, 17, 9, 0), "2026-08-14"),    # 周一 9:00 → 上周五
        (datetime(2026, 8, 17, 15, 0), "2026-08-17"),   # 周一 15:00 → 当日
    ])
    def test_expected_date(self, now, expected):
        assert str(ops_monitor.expected_business_date(now, 14)) == expected


# ── 数据新鲜度（滞后最多 WARNING，绝不 CRITICAL） ──────

class TestDataDateStatus:
    def _status(self, monkeypatch, latest, now, man_dates=()):
        monkeypatch.setattr(ops_monitor, "_db_query", _fake_db({}))
        monkeypatch.setattr(ops_monitor, "_all_manifests", lambda root: [(d, {}) for d in man_dates])
        return ops_monitor.data_date_status(Path("/tmp"), HEALTH_CFG, now)

    def test_fresh_ok(self, monkeypatch):
        monkeypatch.setattr(ops_monitor, "_db_query",
                            _fake_db({"MAX(price_date)": [{"d": "2026-08-13"}]}))
        monkeypatch.setattr(ops_monitor, "_all_manifests", lambda root: [])
        r = ops_monitor.data_date_status(Path("/tmp"), HEALTH_CFG,
                                         datetime(2026, 8, 13, 15, 0))
        assert r["status"] == "ok" and r["latest_data_date"] == "2026-08-13"

    def test_stale_is_warning_only(self, monkeypatch):
        monkeypatch.setattr(ops_monitor, "_db_query",
                            _fake_db({"MAX(price_date)": [{"d": "2026-08-10"}]}))
        monkeypatch.setattr(ops_monitor, "_all_manifests", lambda root: [])
        r = ops_monitor.data_date_status(Path("/tmp"), HEALTH_CFG,
                                         datetime(2026, 8, 13, 15, 0))
        assert r["status"] == "warn"  # 滞后 3 天仍是警告，绝不 CRITICAL
        assert "未及时更新" in r["detail"]

    def test_weekend_compares_to_friday(self, monkeypatch):
        monkeypatch.setattr(ops_monitor, "_db_query",
                            _fake_db({"MAX(price_date)": [{"d": "2026-08-14"}]}))
        monkeypatch.setattr(ops_monitor, "_all_manifests", lambda root: [])
        r = ops_monitor.data_date_status(Path("/tmp"), HEALTH_CFG,
                                         datetime(2026, 8, 15, 15, 0))  # 周六
        assert r["status"] == "ok"

    def test_no_data(self, monkeypatch):
        monkeypatch.setattr(ops_monitor, "_db_query", _fake_db({}))
        monkeypatch.setattr(ops_monitor, "_all_manifests", lambda root: [])
        r = ops_monitor.data_date_status(Path("/tmp"), HEALTH_CFG,
                                         datetime(2026, 8, 13, 15, 0))
        assert r["status"] == "warn" and r["latest_data_date"] is None


# ── 采集器状态 ────────────────────────────────────────

class TestCollectorStatus:
    NOW = datetime(2026, 8, 13, 15, 0)

    def _status(self, monkeypatch, tmp_path, runs):
        monkeypatch.setattr(ops_monitor, "_db_query",
                            _fake_db({"FROM collection_runs": runs}))
        return ops_monitor.collector_status(tmp_path, HEALTH_CFG, self.NOW)

    def test_success(self, monkeypatch, tmp_path):
        _write_manifest(tmp_path, "2026-08-13")
        r = self._status(monkeypatch, tmp_path, [RUN_OK])
        assert r["status"] == "ok" and r["data_date"] == "2026-08-13"
        assert r["expected"] == 40 and r["success"] == 40

    def test_partial_success_warns_with_missing(self, monkeypatch, tmp_path):
        _write_manifest(tmp_path, "2026-08-13", status="partial_success",
                        success=38, failed=2, missing=["锂矿", "钴矿"])
        r = self._status(monkeypatch, tmp_path, [{**RUN_OK, "status": "partial_success"}])
        assert r["status"] == "warn" and r["missing_categories"] == ["锂矿", "钴矿"]

    def test_failed_critical(self, monkeypatch, tmp_path):
        r = self._status(monkeypatch, tmp_path, [{**RUN_OK, "status": "failed"}])
        assert r["status"] == "crit"

    def test_running_within_limit(self, monkeypatch, tmp_path):
        started = (self.NOW - timedelta(minutes=10)).isoformat(timespec="seconds")
        r = self._status(monkeypatch, tmp_path,
                         [{"started_at": started, "finished_at": None,
                           "target_date": "2026-08-13", "status": ""}])
        assert r["status"] == "ok" and "采集中" in str(r["value"])

    def test_running_over_timeout_warns(self, monkeypatch, tmp_path):
        started = (self.NOW - timedelta(minutes=60)).isoformat(timespec="seconds")
        r = self._status(monkeypatch, tmp_path,
                         [{"started_at": started, "finished_at": None,
                           "target_date": "2026-08-13", "status": ""}])
        assert r["status"] == "warn" and "阈值" in r["detail"]

    def test_no_runs(self, monkeypatch, tmp_path):
        r = self._status(monkeypatch, tmp_path, [])
        assert r["status"] == "warn"


# ── 验证 / 固定汇总 ───────────────────────────────────

class TestValidationStatus:
    def test_verdict_mapping(self, tmp_path):
        _write_validation(tmp_path, "2026-08-13", "FAIL", error=2)
        assert ops_monitor.validation_status(tmp_path)["status"] == "crit"
        _write_validation(tmp_path, "2026-08-13", "WARNING", warning=2)
        assert ops_monitor.validation_status(tmp_path)["status"] == "warn"
        _write_validation(tmp_path, "2026-08-13", "PASS")
        assert ops_monitor.validation_status(tmp_path)["status"] == "ok"

    def test_missing(self, tmp_path):
        r = ops_monitor.validation_status(tmp_path)
        assert r["status"] == "warn" and r["verdict"] is None


class TestFixedSummaryStatus:
    def _formal(self, tmp_path):
        p = tmp_path / "data" / "exports" / "固定汇总" / "SMM锂电现货价格_固定汇总.xlsx"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        return p

    def test_updated_formal_ok(self, tmp_path):
        self._formal(tmp_path)
        man = _write_manifest(tmp_path, "2026-08-13")
        r = ops_monitor.fixed_summary_status(tmp_path, man, datetime(2026, 8, 13, 15, 0))
        assert r["status"] == "ok" and r["decision"] == "updated_formal"

    def test_temp_snapshot_warns(self, tmp_path):
        self._formal(tmp_path)
        man = _write_manifest(tmp_path, "2026-08-13", decision="temp_snapshot",
                              formal_updated_at="2026-08-12T12:30:00")
        man["fixed_summary"]["reasons"] = ["日期对齐率 0.3 低于 0.6"]
        r = ops_monitor.fixed_summary_status(tmp_path, man, datetime(2026, 8, 13, 15, 0))
        assert r["status"] == "warn" and "门控" in r["detail"]

    def test_missing_file_warns(self, tmp_path):
        r = ops_monitor.fixed_summary_status(tmp_path, None, datetime(2026, 8, 13, 15, 0))
        assert r["status"] == "warn" and not r["formal_exists"]


# ── 系统资源（monkeypatch 注入边界值） ─────────────────

class TestDiskStatus:
    def _status(self, monkeypatch, avail_blocks):
        st = types.SimpleNamespace(f_blocks=1000, f_frsize=4096, f_bavail=avail_blocks)
        monkeypatch.setattr(ops_monitor.os, "statvfs", lambda path: st)
        return ops_monitor.disk_status(HEALTH_CFG)

    def test_boundaries(self, monkeypatch):
        assert self._status(monkeypatch, 210)["status"] == "ok"     # 79%
        assert self._status(monkeypatch, 200)["status"] == "warn"   # 80%
        assert self._status(monkeypatch, 190)["status"] == "warn"   # 81%
        assert self._status(monkeypatch, 90)["status"] == "crit"    # 91%


class TestMemStatus:
    def _status(self, monkeypatch, avail):
        monkeypatch.setattr(ops_monitor, "_meminfo",
                            lambda: {"MemTotal": 1000, "MemAvailable": avail})
        return ops_monitor.mem_status(HEALTH_CFG)

    def test_boundaries(self, monkeypatch):
        assert self._status(monkeypatch, 500)["status"] == "ok"    # 50%
        assert self._status(monkeypatch, 200)["status"] == "warn"  # 80%
        assert self._status(monkeypatch, 100)["status"] == "crit"  # 90%

    def test_missing_info(self, monkeypatch):
        monkeypatch.setattr(ops_monitor, "_meminfo", lambda: {})
        assert ops_monitor.mem_status(HEALTH_CFG)["status"] == "warn"


class TestCpuStatus:
    def _status(self, monkeypatch, load, total_idle_pairs):
        real_open = open

        def fake_open(path, *a, **kw):
            if str(path) == "/proc/loadavg":
                return io.StringIO(f"{load} 0.10 0.05 1/400 12345")
            return real_open(path, *a, **kw)

        monkeypatch.setattr("builtins.open", fake_open)
        it = iter(total_idle_pairs)
        monkeypatch.setattr(ops_monitor, "_proc_stat", lambda: next(it))
        return ops_monitor.cpu_status(HEALTH_CFG)

    def test_boundaries(self, monkeypatch):
        assert self._status(monkeypatch, 2.9, [(1000, 500), (2000, 1400)])["status"] == "ok"
        assert self._status(monkeypatch, 3.1, [(1000, 500), (2000, 1400)])["status"] == "warn"
        assert self._status(monkeypatch, 4.2, [(1000, 500), (2000, 1400)])["status"] == "crit"
        # CPU 85% 且 load 低 → warn
        assert self._status(monkeypatch, 0.5, [(1000, 500), (2000, 650)])["status"] == "warn"


# ── cron 解析 ─────────────────────────────────────────

class TestNextRun:
    def test_next_weekday(self):
        thursday = datetime(2026, 8, 13, 14, 30)
        n = ops_monitor.next_run("0 10 * * 1-5", thursday)
        assert n == datetime(2026, 8, 14, 10, 0)  # 周五 10:00

    def test_same_day_later(self):
        friday = datetime(2026, 8, 14, 12, 0)
        assert ops_monitor.next_run("30 12 * * 1-5", friday) == datetime(2026, 8, 14, 12, 30)

    def test_weekend_skipped(self):
        friday = datetime(2026, 8, 14, 14, 30)
        n = ops_monitor.next_run("0 10 * * 1-5", friday)
        assert n == datetime(2026, 8, 17, 10, 0)  # 下周一

    def test_step(self):
        t = datetime(2026, 8, 13, 14, 30)
        n = ops_monitor.next_run("*/15 * * * *", t)
        assert n == datetime(2026, 8, 13, 14, 45)

    def test_range(self):
        t = datetime(2026, 8, 13, 8, 0)
        assert ops_monitor.next_run("0 9-11 * * 1-5", t) == datetime(2026, 8, 13, 9, 0)

    def test_malformed_returns_none(self):
        assert ops_monitor.next_run("bad expr", datetime(2026, 8, 13, 12, 0)) is None
        assert ops_monitor.next_run("0 10 * * MON", datetime(2026, 8, 13, 12, 0)) is None


# ── 日志白名单 ────────────────────────────────────────

class TestReadLog:
    ADMIN_CFG = {"admin": {"log_names": {"cron": "logs/cron.log",
                                          "error": "logs/error_{date}.log",
                                          "evil": "logs/../../etc/passwd"},
                           "max_log_lines": 200, "log_days_back": 7}}

    def test_tail_and_cap(self, tmp_path):
        p = tmp_path / "logs" / "cron.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(f"line{i}" for i in range(10)), encoding="utf-8")
        r = ops_monitor.read_log(tmp_path, self.ADMIN_CFG, "cron", 0, 200)
        assert r["ok"] and len(r["lines"]) == 10 and r["lines"][-1] == "line9"
        r2 = ops_monitor.read_log(tmp_path, self.ADMIN_CFG, "cron", 0, 3)
        assert r2["ok"] and len(r2["lines"]) == 3

    def test_date_template_uses_server_clock(self, tmp_path):
        # error_{date} 的日期只由服务器时钟推导：day=1 → 昨天
        r = ops_monitor.read_log(tmp_path, self.ADMIN_CFG, "error", 1)
        assert r["ok"] and r["date"] == (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    def test_unknown_name_and_range(self, tmp_path):
        assert not ops_monitor.read_log(tmp_path, self.ADMIN_CFG, "nope", 0)["ok"]
        assert not ops_monitor.read_log(tmp_path, self.ADMIN_CFG, "cron", 7)["ok"]
        assert not ops_monitor.read_log(tmp_path, self.ADMIN_CFG, "cron", -1)["ok"]

    def test_traversal_template_rejected(self, tmp_path):
        r = ops_monitor.read_log(tmp_path, self.ADMIN_CFG, "evil", 0)
        assert not r["ok"]

    def test_missing_file(self, tmp_path):
        r = ops_monitor.read_log(tmp_path, self.ADMIN_CFG, "cron", 0)
        assert r["ok"] and not r["exists"] and r["lines"] == []


# ── 错误提取 / 验证人类可读化 ─────────────────────────

class TestErrorsAndTransform:
    def test_errors_last_24h(self, tmp_path):
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        for d, msg in ((today, "ERR today line"), (yesterday, "ERR yesterday line")):
            p = tmp_path / "logs" / f"error_{d}.log"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(msg + "\n", encoding="utf-8")
        v = _write_validation(tmp_path, today, "FAIL", error=1)
        v["issues"] = [{"layer": "numeric", "level": "error", "message": "数值错误", "detail": {}}]
        (tmp_path / "logs" / "validation" / f"validation_{today}.json").write_text(
            json.dumps(v, ensure_ascii=False), encoding="utf-8")
        errs = ops_monitor.errors_last_24h(tmp_path)
        msgs = " ".join(e["message"] for e in errs)
        assert "ERR today line" in msgs and "ERR yesterday line" in msgs
        assert any("数值错误" in e["message"] for e in errs)

    def test_transform_validation(self):
        v = {"date": "2026-08-13", "verdict": "WARNING", "generated_at": "x",
             "counts": {"error": 0, "warning": 2, "info": 10},
             "layers": {
                 "file": {"status": "ok", "xlsx_data_rows": 100, "csv_data_rows": 100},
                 "dates": {"status": "warn", "alignment_ratio": 0.74},
                 "categories": {"status": "warn", "missing_count": 1,
                                "missing_categories": ["锂矿"]},
                 "fixed_summary": {"status": "ok", "formal_rows": 100, "expected_rows": 100},
             },
             "issues": [{"layer": "numeric", "level": "error", "message": "m", "detail": {}}]}
        out = ops_monitor.transform_validation(v)
        names = {l["key"]: l for l in out["layers"]}
        assert names["file"]["name"] == "文件完整性"
        assert "100 行" in names["file"]["summary"]
        assert "74%" in names["dates"]["summary"]
        assert "锂矿" in names["categories"]["summary"]
        assert out["issues"][0]["level"] == "error"
        assert ops_monitor.transform_validation(None) is None


# ── 聚合：最差卡片决定整体状态 ────────────────────────

class TestAggregate:
    def _patch_components(self, monkeypatch, statuses):
        components = {
            "web": {"status": statuses.get("web", "ok"), "value": "Port 8888",
                    "detail": "", "port": 8888},
            "collector": {"status": statuses.get("collector", "ok"), "value": "成功",
                          "detail": "", "data_date": None},
            "data_date": {"status": statuses.get("data_date", "ok"), "value": "2026-08-13",
                          "detail": "", "latest_data_date": "2026-08-13", "expected_date": "2026-08-13"},
            "validation": {"status": statuses.get("validation", "ok"), "value": "PASS",
                           "detail": "", "verdict": "PASS", "date": None, "counts": None},
            "fixed_summary": {"status": statuses.get("fixed_summary", "ok"), "value": "正常",
                              "detail": "", "formal_exists": False, "formal_updated_at": None,
                              "decision": None},
            "disk": {"status": statuses.get("disk", "ok"), "value": "50%", "detail": ""},
            "mem": {"status": statuses.get("mem", "ok"), "value": "55%", "detail": ""},
            "cpu": {"status": statuses.get("cpu", "ok"), "value": "10%", "detail": ""},
        }
        monkeypatch.setattr(ops_monitor, "web_status", lambda port, pid=None, timeout=1.5: dict(components["web"]))
        monkeypatch.setattr(ops_monitor, "system_uptimes", lambda pid=None: {})
        monkeypatch.setattr(ops_monitor, "collector_status", lambda r, c, n=None: dict(components["collector"]))
        monkeypatch.setattr(ops_monitor, "validation_status", lambda r: dict(components["validation"]))
        monkeypatch.setattr(ops_monitor, "data_date_status", lambda r, c, n=None: dict(components["data_date"]))
        monkeypatch.setattr(ops_monitor, "fixed_summary_status", lambda r, m, n=None: dict(components["fixed_summary"]))
        monkeypatch.setattr(ops_monitor, "disk_status", lambda c, path="/": dict(components["disk"]))
        monkeypatch.setattr(ops_monitor, "mem_status", lambda c: dict(components["mem"]))
        monkeypatch.setattr(ops_monitor, "cpu_status", lambda c: dict(components["cpu"]))
        monkeypatch.setattr(ops_monitor, "_all_manifests", lambda r: [])
        monkeypatch.setattr(ops_monitor, "tasks", lambda r, c, n=None: [])
        monkeypatch.setattr(ops_monitor, "errors_last_24h", lambda r, limit=20: [])
        return components

    def test_healthy(self, monkeypatch, tmp_path):
        self._patch_components(monkeypatch, {})
        r = ops_monitor.aggregate(HEALTH_CFG, tmp_path, tmp_path / "s.db", 8888,
                                  now=datetime(2026, 8, 13, 15, 0))
        assert r["status"] == "ok" and len(r["cards"]) == 8
        assert all(c["last_checked_at"] for c in r["cards"])

    def test_crit_wins(self, monkeypatch, tmp_path):
        self._patch_components(monkeypatch, {"disk": "crit", "collector": "warn"})
        r = ops_monitor.aggregate(HEALTH_CFG, tmp_path, tmp_path / "s.db", 8888,
                                  now=datetime(2026, 8, 13, 15, 0))
        assert r["status"] == "crit"
        assert any("磁盘" in a["message"] for a in r["alerts"])

    def test_warn_when_no_crit(self, monkeypatch, tmp_path):
        self._patch_components(monkeypatch, {"validation": "warn"})
        r = ops_monitor.aggregate(HEALTH_CFG, tmp_path, tmp_path / "s.db", 8888,
                                  now=datetime(2026, 8, 13, 15, 0))
        assert r["status"] == "warn" and r["status_text"] == "警告"


# ── 任务表 ───────────────────────────────────────────

class TestTasks:
    TASK_CFG = {"tasks": [
        {"key": "daily", "name": "每日采集(早采)", "cron": "0 10 * * 1-5", "source": "manifest_run"},
        {"key": "noon", "name": "全量兜底", "cron": "30 12 * * 1-5", "source": "manifest_run"},
        {"key": "metals", "name": "铜铝镍补采", "cron": "0 11 * * 1-5", "source": "metals_log"},
        {"key": "validation", "name": "数据验证", "cron": "", "source": "validation_log"},
        {"key": "fixed_summary", "name": "固定汇总更新", "cron": "", "source": "fixed_summary"},
        {"key": "mysql_sync", "name": "MySQL 同步", "cron": "", "source": "quality_sync"},
    ]}

    def test_task_statuses(self, monkeypatch, tmp_path):
        runs = [
            {"started_at": "2026-08-13T10:00:01", "finished_at": "2026-08-13T10:02:02",
             "target_date": "2026-08-13", "status": "success"},
            {"started_at": "2026-08-13T12:30:01", "finished_at": "2026-08-13T12:32:03",
             "target_date": "2026-08-13", "status": "success"},
        ]
        monkeypatch.setattr(ops_monitor, "_db_query",
                            _fake_db({"FROM collection_runs": runs}))
        _write_manifest(tmp_path, "2026-08-13")
        qp = tmp_path / "data" / "exports" / "2026" / "08" / "每日汇总" / "Excel" / "SMM数据质量报告_2026-08-13.json"
        qp.parent.mkdir(parents=True, exist_ok=True)
        qp.write_text(json.dumps({"sync": {"status": "success", "batch_id": "b1",
                                           "inserted": 10, "updated": 0, "skipped": 1,
                                           "failed": 0}}, ensure_ascii=False), encoding="utf-8")
        out = {t["key"]: t for t in ops_monitor.tasks(tmp_path, self.TASK_CFG,
                                                       datetime(2026, 8, 13, 15, 0))}
        assert out["daily"]["status"] == "ok" and out["daily"]["last_run"]
        assert out["noon"]["status"] == "ok" and out["noon"]["duration"]
        assert out["daily"]["next_run"] and out["noon"]["next_run"]
        assert out["metals"]["status"] == "warn"      # 无 cron_metals.log
        assert out["validation"]["status"] == "warn"  # 无 validation.log
        assert out["fixed_summary"]["status"] == "ok"
        assert out["mysql_sync"]["status"] == "ok" and out["mysql_sync"]["last_run"] == "b1"
