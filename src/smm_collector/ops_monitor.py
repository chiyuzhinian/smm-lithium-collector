"""管理员运维监控聚合（标准库 /proc 方案，零外部依赖）。

数据来源全部是既有产物，不新建重复数据：
- 采集器：data/exports/*/*/每日汇总/SMM数据状态_<date>.json + SQLite collection_runs
- 验证器：logs/validation/validation_<date>.json
- 最新业务日期：SQLite MAX(price_date)（业务日期，绝不用文件 mtime）
- 系统资源：/proc（meminfo/loadavg/stat/uptime）+ os.statvfs

状态三档：ok(正常) / warn(警告) / crit(异常)；阈值全部由 config 注入。
所有函数显式传参（root/config/db 路径/now），便于单元测试与故障模拟。
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

STATUS_TEXT = {"ok": "正常", "warn": "警告", "crit": "异常"}
RUN_STATUS_MAP = {"success": ("ok", "成功"), "partial_success": ("warn", "部分成功"),
                  "failed": ("crit", "失败")}

# 验证 9 层的中文名（与 daily_validation.py 的 layers key 对齐）
LAYER_NAMES = {
    "file": "文件完整性", "schema": "Schema 校验", "dates": "日期一致性",
    "duplicates": "重复检测", "categories": "分类完整性", "numeric": "数值检查",
    "volatility": "异常波动", "continuity": "历史连续性",
    "fixed_summary": "固定汇总一致性",
}


# ── 基础工具 ──────────────────────────────────────────

def _parse_ts(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        try:
            return datetime.strptime(str(v), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def format_duration(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def _db_query(db_path: Path, sql: str, params: tuple = ()) -> list[dict]:
    """只读 SQLite 查询；数据库不可用返回空列表。"""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        with conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        return []


def _all_manifests(root: Path) -> list[tuple[str, dict]]:
    """(date, manifest) 列表，按日期降序。"""
    out = []
    try:
        for p in (root / "data" / "exports").glob("*/*/每日汇总/SMM数据状态_*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            out.append((p.name[len("SMM数据状态_"):-len(".json")], data))
    except OSError:
        pass
    return sorted(out, key=lambda x: x[0], reverse=True)


def _latest_validation(root: Path) -> dict | None:
    try:
        files = sorted((root / "logs" / "validation").glob("validation_*.json"), reverse=True)
    except OSError:
        return None
    for p in files:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def _quality_report_sync(root: Path, man_date: str | None) -> dict | None:
    """读取当日质量报告中的 MySQL 同步段。"""
    if not man_date:
        return None
    p = (root / "data" / "exports" / man_date[:4] / man_date[5:7]
         / "每日汇总" / "Excel" / f"SMM数据质量报告_{man_date}.json")
    try:
        return (json.loads(p.read_text(encoding="utf-8")) or {}).get("sync")
    except Exception:
        return None


# ── Web 与系统状态 ────────────────────────────────────

def system_uptimes(pid: int | None = None) -> dict:
    """Linux 运行时间与 Web 进程运行时间（秒）。"""
    out = {"linux_uptime_seconds": None, "process_uptime_seconds": None}
    try:
        up = float(open("/proc/uptime", encoding="utf-8").read().split()[0])
        out["linux_uptime_seconds"] = int(up)
    except OSError:
        return out
    if pid:
        try:
            data = open(f"/proc/{pid}/stat", encoding="utf-8").read()
            after = data.rsplit(")", 1)[1].split()
            starttime = int(after[19])  # field 22（starttime）在 comm 之后为下标 19
            hz = os.sysconf("SC_CLK_TCK")
            out["process_uptime_seconds"] = int(up - starttime / hz)
        except (OSError, ValueError, IndexError):
            pass
    return out


def web_status(port: int, pid: int | None = None, timeout: float = 1.5) -> dict:
    """Web 服务自检：进程存活 + 本地端口接受极简 /health 探测（无 HTTP 递归）。"""
    out = {"status": "ok", "value": f"Port {port}", "detail": "", "port": port, "pid": pid}
    if pid and not os.path.exists(f"/proc/{pid}"):
        out.update(status="crit", detail="Web 进程不存在")
        return out
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
            s.sendall(b"GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
            resp = s.recv(512).decode("utf-8", "replace")
            first = resp.split("\r\n", 1)[0] if resp else ""
            if " 200 " not in first:
                out.update(status="crit", detail=f"本地健康探测异常: {first or '无响应'}")
    except OSError:
        out.update(status="crit", detail=f"端口 {port} 无响应")
    return out


# ── 采集器状态 ────────────────────────────────────────

def collector_status(root: Path, config: dict, now: datetime | None = None) -> dict:
    """采集器最近运行状态（collection_runs + 最新 manifest）。"""
    now = now or datetime.now()
    out = {"status": "warn", "value": "—", "detail": "",
           "data_date": None, "last_run_started_at": None, "last_run_finished_at": None,
           "duration_seconds": None, "expected": 0, "success": 0, "failed": 0,
           "missing_categories": [], "valid": 0, "warning": 0, "invalid": 0,
           "manifest_exists": False, "runs": []}
    manifests = _all_manifests(root)
    man_date, man = (manifests[0] if manifests else (None, None))
    out["manifest_exists"] = man is not None
    if man:
        c = man.get("collection") or {}
        out.update(data_date=man.get("target_date") or man_date,
                   expected=c.get("categories_expected", 0),
                   success=c.get("categories_succeeded", 0),
                   failed=c.get("categories_failed", 0),
                   missing_categories=c.get("missing_categories", []),
                   valid=c.get("valid_count", 0), warning=c.get("warning_count", 0),
                   invalid=c.get("invalid_count", 0),
                   runs=man.get("runs", []))

    runs = _db_query(root / "data" / "database" / "smm_lithium.db",
                     "SELECT started_at, finished_at, target_date, status"
                     " FROM collection_runs ORDER BY started_at DESC LIMIT 1")
    run = runs[0] if runs else None
    out["last_run_started_at"] = run.get("started_at") if run else None
    out["last_run_finished_at"] = run.get("finished_at") if run else None
    started = _parse_ts(run.get("started_at")) if run else None
    finished = _parse_ts(run.get("finished_at")) if run else None

    if run and not run.get("finished_at") and started:
        elapsed = (now - started).total_seconds()
        limit = int(config.get("collector_timeout_minutes", 45)) * 60
        if elapsed > limit:
            out.update(status="warn", value="采集中(超时)",
                       detail=f"采集运行已 {format_duration(elapsed)}，超过阈值 {format_duration(limit)}")
        else:
            out.update(status="ok", value="采集中",
                       detail=f"采集运行中，已 {format_duration(elapsed)}")
    elif run and started and finished:
        status, text = RUN_STATUS_MAP.get(run.get("status") or "", ("warn", str(run.get("status"))))
        out.update(status=status, value=text,
                   duration_seconds=(finished - started).total_seconds(),
                   detail=f"{started.strftime('%m-%d %H:%M')} → {finished.strftime('%H:%M')}"
                          f"（{format_duration((finished - started).total_seconds())}）")
    elif run:
        out.update(status="warn", value="无结束记录", detail="最近一次采集无结束时间记录")
    else:
        out.update(value="无记录", detail="暂无采集运行记录")
    if not out["data_date"] and run:
        out["data_date"] = run.get("target_date")
    return out


# ── 数据验证 / 固定汇总 / 业务日期 ─────────────────────

def validation_status(root: Path) -> dict:
    v = _latest_validation(root)
    if v is None:
        return {"status": "warn", "value": "无记录", "detail": "暂无验证记录",
                "verdict": None, "date": None, "counts": None}
    verdict = v.get("verdict")
    status = {"PASS": "ok", "WARNING": "warn", "FAIL": "crit"}.get(verdict, "warn")
    counts = v.get("counts") or {}
    return {"status": status, "value": verdict, "detail":
            f"通过 {counts.get('info', 0)} · 警告 {counts.get('warning', 0)}"
            f" · 失败 {counts.get('error', 0)}",
            "verdict": verdict, "date": v.get("date"), "counts": counts,
            "generated_at": v.get("generated_at")}


def fixed_summary_status(root: Path, latest_manifest: dict | None,
                         now: datetime | None = None) -> dict:
    formal = root / "data" / "exports" / "固定汇总" / "SMM锂电现货价格_固定汇总.xlsx"
    out = {"status": "warn", "value": "未更新", "detail": "",
           "formal_exists": False, "formal_updated_at": None, "decision": None}
    if not formal.is_file():
        out["detail"] = "固定汇总文件未生成"
        return out
    out["formal_exists"] = True
    out["formal_updated_at"] = datetime.fromtimestamp(
        formal.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    fs = (latest_manifest or {}).get("fixed_summary") or {}
    out["decision"] = fs.get("decision")
    if fs.get("decision") == "updated_formal":
        out.update(status="ok", value="正常",
                   detail=f"最后更新 {fs.get('formal_updated_at') or out['formal_updated_at']}")
    elif fs.get("decision") == "temp_snapshot":
        reasons = "；".join(fs.get("reasons") or []) or "门控未通过"
        out.update(value="临时快照", detail=f"门控未通过，正式文件未更新：{reasons}")
    else:
        out["detail"] = f"文件存在，最后更新 {out['formal_updated_at']}"
        # 文件 mtime 早于最新业务日期 → 未随最新数据更新
        mdate = (latest_manifest or {}).get("target_date")
        if mdate and out["formal_updated_at"][:10] < mdate:
            out.update(value="未更新", detail=f"未随最新数据（{mdate}）更新")
    return out


def expected_business_date(now: datetime, check_hour: int = 14) -> datetime.date:
    """保守的期望业务日期：工作日且已过检查时点 → 当日；否则取最近一个工作日。"""
    d = now.date()
    if now.weekday() < 5 and now.hour >= check_hour:
        candidate = d
    else:
        candidate = d - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def data_date_status(root: Path, config: dict, now: datetime | None = None) -> dict:
    """最新业务数据日期 + 新鲜度（滞后最多 WARNING，绝不 CRITICAL）。"""
    now = now or datetime.now()
    rows = _db_query(root / "data" / "database" / "smm_lithium.db",
                     "SELECT MAX(price_date) AS d FROM lithium_spot_prices")
    db_latest = str(rows[0]["d"]) if rows and rows[0]["d"] else None
    manifests = _all_manifests(root)
    man_latest = manifests[0][0] if manifests else None
    latest = max((d for d in (db_latest, man_latest) if d), default=None)
    expected = expected_business_date(now, int(config.get("freshness_check_hour", 14)))
    if latest is None:
        return {"status": "warn", "value": "无数据", "detail": "数据库与导出均无数据",
                "latest_data_date": None, "expected_date": str(expected)}
    if latest >= str(expected):
        return {"status": "ok", "value": latest, "detail": f"期望日期 {expected}",
                "latest_data_date": latest, "expected_date": str(expected)}
    return {"status": "warn", "value": latest,
            "detail": f"数据可能未及时更新（最新 {latest}，期望 {expected}）",
            "latest_data_date": latest, "expected_date": str(expected)}


# ── 系统资源（/proc，零依赖） ──────────────────────────

def disk_status(config: dict, path: str = "/") -> dict:
    st = os.statvfs(path)
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = total - free
    pct = used / total * 100 if total else 0.0
    warn_pct = float(config.get("disk_warn_pct", 80))
    crit_pct = float(config.get("disk_crit_pct", 90))
    status = "crit" if pct >= crit_pct else "warn" if pct >= warn_pct else "ok"
    gb = 1024 ** 3
    return {"status": status, "value": f"{pct:.0f}%",
            "detail": f"{used / gb:.1f} GB / {total / gb:.1f} GB（可用 {free / gb:.1f} GB）",
            "total_gb": round(total / gb, 1), "used_gb": round(used / gb, 1),
            "free_gb": round(free / gb, 1), "usage_percent": round(pct, 1)}


def _meminfo() -> dict:
    out = {}
    try:
        for line in open("/proc/meminfo", encoding="utf-8"):
            k, _, v = line.partition(":")
            try:
                out[k] = int(v.split()[0]) * 1024
            except (ValueError, IndexError):
                pass
    except OSError:
        pass
    return out


def mem_status(config: dict) -> dict:
    info = _meminfo()
    total = info.get("MemTotal")
    avail = info.get("MemAvailable")
    if not total:
        return {"status": "warn", "value": "—", "detail": "无法读取 /proc/meminfo",
                "total_gb": None, "used_gb": None, "usage_percent": None}
    used = total - (avail if avail is not None else 0)
    pct = used / total * 100
    warn_pct = float(config.get("mem_warn_pct", 80))
    crit_pct = float(config.get("mem_crit_pct", 90))
    status = "crit" if pct >= crit_pct else "warn" if pct >= warn_pct else "ok"
    gb = 1024 ** 3
    return {"status": status, "value": f"{pct:.0f}%",
            "detail": f"{used / gb:.1f} GB / {total / gb:.1f} GB",
            "total_gb": round(total / gb, 1), "used_gb": round(used / gb, 1),
            "usage_percent": round(pct, 1)}


def _proc_stat() -> tuple[float | None, float | None]:
    """/proc/stat 首行：(total, idle)。"""
    try:
        vals = [int(x) for x in open("/proc/stat", encoding="utf-8").readline().split()[1:9]]
        return sum(vals), vals[3] + vals[4]  # idle + iowait
    except (OSError, ValueError, IndexError):
        return None, None


def cpu_status(config: dict) -> dict:
    """load average（/proc/loadavg）+ 瞬时占用率（/proc/stat 双采样 0.15s）。"""
    load1 = load5 = load15 = None
    try:
        parts = open("/proc/loadavg", encoding="utf-8").read().split()
        load1, load5, load15 = (float(parts[i]) for i in range(3))
    except (OSError, ValueError, IndexError):
        pass
    total1, idle1 = _proc_stat()
    time.sleep(0.15)
    total2, idle2 = _proc_stat()
    cpu_pct = None
    if None not in (total1, idle1, total2, idle2) and total2 > total1:
        cpu_pct = 100 * (1 - (idle2 - idle1) / (total2 - total1))
    load_warn = float(config.get("load_warn", 3.0))
    load_crit = float(config.get("load_crit", 4.0))
    if (load1 is not None and load1 >= load_crit) or (cpu_pct is not None and cpu_pct >= 95):
        status = "crit"
    elif (load1 is not None and load1 >= load_warn) or (cpu_pct is not None and cpu_pct >= 80):
        status = "warn"
    else:
        status = "ok"
    value = f"{cpu_pct:.0f}%" if cpu_pct is not None else "—"
    load_txt = "/".join(f"{x:.2f}" for x in (load1, load5, load15) if x is not None)
    return {"status": status, "value": value, "cpu_percent": round(cpu_pct, 1) if cpu_pct else None,
            "load1": load1, "load5": load5, "load15": load15,
            "detail": f"load {load_txt}（{os.cpu_count() or '?'} 核）"}


# ── cron 解析 ─────────────────────────────────────────

def _field_match(value: int, expr: str, lo: int, hi: int) -> bool:
    """cron 单字段匹配：支持 *、a-b、a-b/n、*/n、列表。"""
    for part in expr.split(","):
        try:
            base, _, step_s = part.partition("/")
            step = int(step_s) if step_s else 1
            if step <= 0:
                continue
            if base == "*":
                a, b = lo, hi
            elif "-" in base:
                a, b = (int(x) for x in base.split("-", 1))
            else:
                a = b = int(base)
            a, b = max(a, lo), min(b, hi)
            if a <= value <= b and (value - a) % step == 0:
                return True
        except (ValueError, TypeError):
            continue
    return False


def _field_any(expr: str) -> bool:
    return any(part.split("/", 1)[0] == "*" for part in expr.split(","))


def next_run(cron_expr: str, now: datetime) -> datetime | None:
    """下次执行时间（5 字段 cron：分 时 日 月 周；日/周双限定取 OR，标准语义）。

    限制：不支持月份/星期英文名与 L/W/# 特殊字符（本项目任务仅用数值表达式）。
    8 天内无匹配返回 None。
    """
    try:
        m_f, h_f, dom_f, mon_f, dow_f = cron_expr.split()
    except (ValueError, AttributeError):
        return None
    if not all(set(f) <= set("0123456789*,-/") for f in (m_f, h_f, dom_f, mon_f, dow_f)):
        return None
    dom_any, dow_any = _field_any(dom_f), _field_any(dow_f)
    for i in range(1, 8 * 24 * 60 + 1):
        t = now + timedelta(minutes=i)
        if not _field_match(t.minute, m_f, 0, 59):
            continue
        if not _field_match(t.hour, h_f, 0, 23):
            continue
        if not _field_match(t.month, mon_f, 1, 12):
            continue
        dom_ok = _field_match(t.day, dom_f, 1, 31)
        dow_ok = _field_match((t.weekday() + 1) % 7, dow_f, 0, 7)
        if dom_any and dow_any:
            day_ok = True
        elif dom_any:
            day_ok = dow_ok
        elif dow_any:
            day_ok = dom_ok
        else:
            day_ok = dom_ok or dow_ok
        if not day_ok:
            continue
        return t
    return None


# ── 任务状态 ──────────────────────────────────────────

def _metals_log_status(root: Path, now: datetime) -> dict:
    p = root / "logs" / "cron_metals.log"
    if not p.is_file():
        return {"last_run": None, "status": "warn", "summary": "无日志文件"}
    mt = datetime.fromtimestamp(p.stat().st_mtime)
    last_line = "".join(_tail(p, 1)).strip()
    if mt.date() == now.date():
        return {"last_run": mt.strftime("%Y-%m-%d %H:%M:%S"), "status": "ok",
                "summary": last_line[:120] or "已运行"}
    return {"last_run": mt.strftime("%Y-%m-%d %H:%M:%S"), "status": "warn",
            "summary": f"最近运行于 {mt.strftime('%m-%d %H:%M')}（今日未见记录）"}


def _validation_log_status(root: Path) -> dict:
    p = root / "logs" / "validation" / "validation.log"
    if not p.is_file():
        return {"last_run": None, "status": "warn", "summary": "无验证日志"}
    mt = datetime.fromtimestamp(p.stat().st_mtime)
    line = "".join(_tail(p, 1)).strip()
    verdict = "FAIL"
    for token in ("PASS", "WARNING", "FAIL"):
        if f"VERDICT={token}" in line:
            verdict = token
    status = {"PASS": "ok", "WARNING": "warn", "FAIL": "crit"}[verdict]
    return {"last_run": mt.strftime("%Y-%m-%d %H:%M:%S"), "status": status,
            "summary": line[:160]}


def tasks(root: Path, config: dict, now: datetime | None = None) -> list[dict]:
    """定时/跟随任务状态表（配置定义 + 既有产物推导最近执行）。"""
    now = now or datetime.now()
    manifests = _all_manifests(root)
    man_date, man = (manifests[0] if manifests else (None, None))
    runs = _db_query(root / "data" / "database" / "smm_lithium.db",
                     "SELECT started_at, finished_at, target_date, status"
                     " FROM collection_runs ORDER BY started_at")
    day_runs = [r for r in runs if r.get("target_date") == (man_date or "")]
    daily = day_runs[0] if day_runs else None
    noon = day_runs[-1] if len(day_runs) >= 2 else None
    sync = _quality_report_sync(root, man_date)
    fs = (man or {}).get("fixed_summary") or {}
    metals = _metals_log_status(root, now)
    validation = _validation_log_status(root)

    def run_item(run: dict | None) -> dict:
        if not run:
            return {"last_run": None, "status": "warn", "summary": "今日未见运行记录",
                    "duration": None}
        started = _parse_ts(run.get("started_at"))
        finished = _parse_ts(run.get("finished_at"))
        status, text = RUN_STATUS_MAP.get(run.get("status") or "", ("warn", str(run.get("status"))))
        dur = (finished - started).total_seconds() if started and finished else None
        return {"last_run": run.get("finished_at") or run.get("started_at"),
                "status": status, "summary": text, "duration": format_duration(dur)}

    out = []
    for t in config.get("tasks") or []:
        key = t.get("key", "")
        cron = str(t.get("cron") or "")
        nxt = next_run(cron, now) if cron else None
        item = {"key": key, "name": t.get("name", key), "cron": cron,
                "next_run": nxt.strftime("%Y-%m-%d %H:%M") if nxt else None,
                "last_run": None, "status": "ok", "summary": "", "duration": None}
        source = t.get("source", "")
        if source == "manifest_run":
            item.update(run_item(daily if key == "daily" else noon))
        elif source == "metals_log":
            item.update(metals)
        elif source == "validation_log":
            item.update(validation)
        elif source == "fixed_summary":
            if fs.get("decision") == "updated_formal":
                item.update(last_run=fs.get("formal_updated_at"), status="ok",
                            summary="正式文件已更新")
            elif fs.get("decision") == "temp_snapshot":
                item.update(last_run=fs.get("temp_snapshot_path"), status="warn",
                            summary="门控未通过，仅临时快照")
            else:
                item.update(status="warn", summary="无更新记录")
        elif source == "quality_sync":
            if sync:
                s_status = str(sync.get("status") or sync.get("sync_status") or "")
                item.update(last_run=sync.get("batch_id"),
                            status="ok" if s_status == "success" else "warn",
                            summary=f"inserted={sync.get('inserted')} updated={sync.get('updated')}"
                                    f" skipped={sync.get('skipped')} failed={sync.get('failed')}")
            else:
                item.update(status="warn", summary="无同步记录")
        out.append(item)
    return out


# ── 日志与错误 ────────────────────────────────────────

def _tail(path: Path, n: int) -> list[str]:
    """读取文件末尾 n 行（按字节反向扫描，不整读大文件）。"""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block, data = 8192, b""
            while size > 0 and data.count(b"\n") < n:
                read_len = min(block, size)
                size -= read_len
                f.seek(size)
                data = f.read(read_len) + data
            return data.decode("utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def read_log(root: Path, config: dict, name: str, day_offset: int = 0,
             lines: int | None = None) -> dict:
    """按白名单读日志尾部。name 必须在 admin.log_names 中；
    {date} 只由服务器时钟推导，绝不接受客户端路径输入。"""
    admin_cfg = config.get("admin") or {}
    log_names = admin_cfg.get("log_names") or {}
    if name not in log_names:
        return {"ok": False, "error": "未知日志名"}
    try:
        day = int(day_offset or 0)
    except (TypeError, ValueError):
        day = 0
    max_days = int(admin_cfg.get("log_days_back", 7))
    if not 0 <= day < max_days:
        return {"ok": False, "error": "日期超出允许范围"}
    max_lines = int(admin_cfg.get("max_log_lines", 200))
    n = min(int(lines or max_lines), max_lines) if lines else max_lines
    date_str = (datetime.now() - timedelta(days=day)).strftime("%Y-%m-%d")
    rel = log_names[name].format(date=date_str)
    p = (root / rel).resolve()
    logs_root = (root / "logs").resolve()
    if not p.is_relative_to(logs_root):
        return {"ok": False, "error": "非法路径"}
    return {"ok": True, "name": name, "path": rel, "day_offset": day,
            "date": date_str, "exists": p.is_file(), "lines": _tail(p, n)}


def errors_last_24h(root: Path, limit: int = 20) -> list[dict]:
    """最近错误摘要：今日/昨日 error 日志尾部 + 最新验证 error issues。"""
    out = []
    for d in (0, 1):
        date_str = (datetime.now() - timedelta(days=d)).strftime("%Y-%m-%d")
        p = root / "logs" / f"error_{date_str}.log"
        if p.is_file():
            for line in _tail(p, 40):
                if line.strip():
                    out.append({"source": f"error_{date_str}.log",
                                "level": "error", "message": line[:300]})
    v = _latest_validation(root)
    if v:
        for i in v.get("issues") or []:
            if i.get("level") == "error":
                out.append({"source": "validator", "level": "error",
                            "message": f"[{i.get('layer')}] {i.get('message', '')}"[:300]})
    return out[:limit]


# ── 验证结果人类可读化 ────────────────────────────────

def _layer_summary(key: str, layer: dict) -> str:
    """把层内关键字段压缩成一句话。"""
    if key == "file":
        return f"Excel {layer.get('xlsx_data_rows')} 行 · CSV {layer.get('csv_data_rows')} 行"
    if key == "schema" and layer.get("missing_columns"):
        return "缺列: " + ", ".join(layer["missing_columns"][:5])
    if key == "dates" and layer.get("alignment_ratio") is not None:
        return f"对齐率 {layer['alignment_ratio']:.0%}"
    if key == "categories" and layer.get("missing_count"):
        head = "、".join(layer.get("missing_categories", [])[:5])
        return f"缺失 {layer['missing_count']} 个: {head}"
    if key == "fixed_summary" and layer.get("formal_rows") is not None:
        return f"正式 {layer['formal_rows']} 行 vs 期望 {layer.get('expected_rows')} 行"
    return "通过" if layer.get("status") in ("ok", "pass") else "异常"


def transform_validation(vjson: dict | None) -> dict | None:
    """把验证 JSON 转成前端易读结构（层中文名 + 状态 + 摘要 + issues）。"""
    if not vjson:
        return None
    layers = []
    for key, name in LAYER_NAMES.items():
        lv = (vjson.get("layers") or {}).get(key)
        if not lv:
            continue
        layers.append({"key": key, "name": name, "status": lv.get("status"),
                       "summary": _layer_summary(key, lv)})
    issues = []
    for i in vjson.get("issues") or []:
        detail = i.get("detail")
        issues.append({"layer": i.get("layer"), "level": i.get("level"),
                       "message": i.get("message"),
                       "detail": json.dumps(detail, ensure_ascii=False, default=str)
                       if detail else ""})
    return {"date": vjson.get("date"), "verdict": vjson.get("verdict"),
            "generated_at": vjson.get("generated_at"),
            "counts": vjson.get("counts"), "layers": layers, "issues": issues}


# ── 聚合 ─────────────────────────────────────────────

def aggregate(config: dict, root: Path, db_path: Path, port: int,
              pid: int | None = None, now: datetime | None = None) -> dict:
    """运维总览聚合：8 张状态卡 + 告警 + 今日采集 + 最近任务/错误。

    overall = 最差卡片状态（crit > warn > ok）；阈值全部来自 config 的 health 段。
    """
    now = now or datetime.now()
    checked = now.strftime("%H:%M:%S")
    manifests = _all_manifests(root)
    man_date, man = (manifests[0] if manifests else (None, None))
    web = web_status(port, pid)
    web.update(system_uptimes(pid))
    collector = collector_status(root, config, now)
    validation = validation_status(root)
    cards = [
        {"key": "web", "name": "Web服务", **web, "last_checked_at": checked},
        {"key": "collector", "name": "SMM采集器", **collector, "last_checked_at": checked},
        {"key": "data_date", "name": "最新业务数据",
         **data_date_status(root, config, now), "last_checked_at": checked},
        {"key": "validation", "name": "数据验证", **validation, "last_checked_at": checked},
        {"key": "fixed_summary", "name": "固定汇总",
         **fixed_summary_status(root, man, now), "last_checked_at": checked},
        {"key": "disk", "name": "磁盘", **disk_status(config), "last_checked_at": checked},
        {"key": "mem", "name": "内存", **mem_status(config), "last_checked_at": checked},
        {"key": "cpu", "name": "CPU", **cpu_status(config), "last_checked_at": checked},
    ]
    alerts = [{"level": c["status"], "message": f"{c['name']}: {c['detail']}"}
              for c in cards if c["status"] != "ok"]
    today_collection = {"date": man_date}
    if man:
        c = man.get("collection") or {}
        today_collection.update(expected=c.get("categories_expected", 0),
                                success=c.get("categories_succeeded", 0),
                                failed=c.get("categories_failed", 0),
                                valid=c.get("valid_count", 0),
                                warning=c.get("warning_count", 0),
                                invalid=c.get("invalid_count", 0),
                                missing_categories=c.get("missing_categories", []))
        if c.get("missing_categories"):
            alerts.append({"level": "warn",
                           "message": f"今日采集缺失 {len(c['missing_categories'])} 个分类: "
                                      + "、".join(c["missing_categories"][:5])})
    overall = "ok"
    for c in cards:
        if c["status"] == "crit":
            overall = "crit"
        elif c["status"] == "warn" and overall == "ok":
            overall = "warn"
    return {"status": overall, "status_text": STATUS_TEXT[overall],
            "cards": cards, "alerts": alerts,
            "today_collection": today_collection,
            "recent_tasks": tasks(root, config, now)[:8],
            "recent_errors": errors_last_24h(root),
            "last_checked_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "meta": {"generated_at": now.isoformat(timespec="seconds")}}


_agg_cache = {"ts": 0.0, "data": None}
_agg_lock = threading.Lock()


def clear_cache() -> None:
    """清空聚合缓存（测试用）。"""
    with _agg_lock:
        _agg_cache["data"] = None
        _agg_cache["ts"] = 0.0


def aggregate_cached(config: dict, root: Path, db_path: Path, port: int,
                     pid: int | None = None, now: datetime | None = None) -> dict:
    """带 TTL 的聚合（默认 30s），避免管理员轮询压服务器。"""
    ttl = float((config.get("health") or {}).get("cache_seconds", 30))
    with _agg_lock:
        if _agg_cache["data"] is not None and time.time() - _agg_cache["ts"] < ttl:
            return _agg_cache["data"]
    data = aggregate(config, root, db_path, port, pid, now)
    with _agg_lock:
        _agg_cache["ts"] = time.time()
        _agg_cache["data"] = data
    return data
