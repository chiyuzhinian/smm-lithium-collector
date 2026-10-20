"""钉钉业务日报数据层测试：40 种产品覆盖 / 新旧报价判定 / 最近报价 /
消息格式与拆分 / Excel 完整性 / 与 portal_service 口径一致性。"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from openpyxl import load_workbook

from smm_collector import business_daily as bd
from smm_collector import portal_service as ps

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "config"

DATA_DATE = "2026-10-20"
STARTED_AT = "2026-10-20T09:05:00"


def _row(category, product_name, specification, unit, price_date, avg,
         collected_at=None, change=0, min_p=None, max_p=None):
    avg_t = None if avg is None else str(avg)
    return {
        "source": "SMM", "market": "SMM锂电现货", "category": category,
        "product_name": product_name, "specification": specification,
        "min_price": str(min_p) if min_p is not None else avg_t,
        "max_price": str(max_p) if max_p is not None else avg_t,
        "average_price": avg_t, "change_value": str(change),
        "unit": unit, "price_date": price_date,
        "collected_at": collected_at or f"{price_date}T09:05:00",
        "record_hash": f"h-{category}-{product_name}-{specification}-{price_date}",
        "validation_status": "valid", "created_at": "now", "updated_at": "now",
    }


@pytest.fixture()
def db_path(tmp_path):
    """合成库：新报价 / 旧报价 / 周报价 / 无历史 / 双分类重复 各场景。"""
    path = tmp_path / "smm_test.db"
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE lithium_spot_prices (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source TEXT, market TEXT, category TEXT, product_name TEXT, specification TEXT DEFAULT '',
      min_price TEXT, max_price TEXT, average_price TEXT, change_value TEXT, unit TEXT DEFAULT '',
      price_date TEXT, collected_at TEXT, source_url TEXT, collection_method TEXT, raw_text TEXT,
      extra_fields TEXT, record_hash TEXT, validation_status TEXT, validation_message TEXT,
      created_at TEXT, updated_at TEXT)""")
    rows = [
        # 本次有效新报价（含磷化工双分类重复收录，去重只计一次）
        _row("锂化合物", "电池级碳酸锂", "Li₂CO₃≥99.5%", "元/吨", DATA_DATE,
             156000, collected_at=f"{DATA_DATE}T09:06:00", change=-200,
             min_p=155500, max_p=156500),
        _row("磷化工", "电池级碳酸锂", "Li₂CO₃≥99.5%", "元/吨", DATA_DATE,
             156000, collected_at=f"{DATA_DATE}T09:06:00", change=-200,
             min_p=155500, max_p=156500),
        # 数据日当天有行但本次采集未刷新（collected_at 早于 started_at）→ 本次无新报价
        _row("基础金属", "1#电解铜", "", "元/吨", DATA_DATE,
             78320, collected_at=f"{DATA_DATE}T08:50:00", change=0),
        _row("基础金属", "1#电解铜", "", "元/吨", "2026-10-17",
             78000, change=100),
        # 仅昨日报价 → 本次无新报价 + 最近有效报价
        _row("基础金属", "A00铝", "", "元/吨", "2026-10-17",
             20840, change=120),
        # 周频电芯：最近发布于上周五 → 本次无新报价 + 最近有效报价（真实日期）
        _row("电芯", "方形磷酸铁锂电芯(周)", "100Ah", "元/Wh", "2026-10-16",
             "0.40", change="0.01"),
        # 无效行必须被排除（同日 valid 之外存在 invalid，不得影响最近报价）
        _row("锂化合物", "工业级碳酸锂", "Li₂CO₃≥99.2%", "元/吨", "2026-10-17",
             999999, change=0),
    ]
    for r in rows:
        r["validation_status"] = "invalid" if r["average_price"] == "999999" else "valid"
    for r in rows:
        con.execute("""INSERT INTO lithium_spot_prices
          (source, market, category, product_name, specification, min_price, max_price,
           average_price, change_value, unit, price_date, collected_at, record_hash,
           validation_status, created_at, updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (r["source"], r["market"], r["category"], r["product_name"], r["specification"],
           r["min_price"], r["max_price"], r["average_price"], r["change_value"],
           r["unit"], r["price_date"], r["collected_at"], r["record_hash"],
           r["validation_status"], "now", "now"))
    con.commit()
    con.close()
    return path


@pytest.fixture(scope="module")
def products():
    prods, meta = bd.load_products(CONFIG_ROOT)
    assert meta["load_errors"] == [], meta["load_errors"]
    return prods


def _con(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _report(db_path, products):
    con = _con(db_path)
    try:
        return bd.build_report(products, con, DATA_DATE, STARTED_AT)
    finally:
        con.close()


# ── 场景 A/B/C/D：覆盖与新/旧判定 ──────────────────────────

def test_report_covers_all_40_products(db_path, products):
    rows = _report(db_path, products)
    assert len(rows) == 40
    assert len({r["id"] for r in rows}) == 40
    new = [r for r in rows if r["status"] == bd.STATUS_NEW]
    assert [r["id"] for r in new] == ["lce_battery"]


def test_new_quote_fields_from_same_row(db_path, products):
    row = {r["id"]: r for r in _report(db_path, products)}["lce_battery"]
    q = row["quote"]
    assert q["price_date"] == DATA_DATE
    assert q["min_price"] == "155500"
    assert q["max_price"] == "156500"
    assert q["average_price"] == "156000"
    assert q["change_value"] == "-200"
    assert q["unit"] == "元/吨"


def test_stale_same_day_row_counts_as_missing(db_path, products):
    """数据日当天有行但本次采集未刷新（collected_at < started_at）→ 不得冒充本次新报价。"""
    row = {r["id"]: r for r in _report(db_path, products)}["cu_1e"]
    assert row["status"] == bd.STATUS_MISSING
    assert row["last_valid"]["price_date"] == "2026-10-17"
    assert row["last_valid"]["average_price"] == "78000"


def test_missing_with_last_valid_keeps_real_date(db_path, products):
    row = {r["id"]: r for r in _report(db_path, products)}["al_a00"]
    assert row["status"] == bd.STATUS_MISSING
    assert row["last_valid"]["price_date"] == "2026-10-17"
    assert row["last_valid"]["average_price"] == "20840"


def test_weekly_product_missing_shows_last_real_quote(db_path, products):
    row = {r["id"]: r for r in _report(db_path, products)}["cell_lfp_100ah"]
    assert row["status"] == bd.STATUS_MISSING
    assert row["last_valid"]["price_date"] == "2026-10-16"
    assert row["last_valid"]["average_price"] == "0.40"


def test_no_history_product_flagged(db_path, products):
    row = {r["id"]: r for r in _report(db_path, products)}["lfp_repair"]
    assert row["status"] == bd.STATUS_NO_HISTORY
    assert row["quote"] is None and row["last_valid"] is None


def test_unavailable_merged_always_no_history(db_path, products):
    row = {r["id"]: r for r in _report(db_path, products)}["lfp_ess_250"]
    assert row["status"] == bd.STATUS_NO_HISTORY


# ── 场景 E：与门户查询口径逐项一致 ──────────────────────────

def test_consistency_with_portal_service(db_path, products):
    con = _con(db_path)
    try:
        rows = bd.build_report(products, con, DATA_DATE, STARTED_AT)
        portal = ps.latest_quote(products, con, as_of=DATA_DATE)
        for r in rows:
            if r["status"] == bd.STATUS_NEW:
                assert r["quote"] == portal.get(r["id"]), r["id"]
            elif r["status"] == bd.STATUS_MISSING and r["last_valid"]:
                # 最近有效报价 = as_of 前一日 的门户口径最新报价
                expect = ps.latest_quote([_by_id(products, r["id"])], con, as_of="2026-10-19")
                assert r["last_valid"] == expect.get(r["id"]), r["id"]
    finally:
        con.close()


def _by_id(products, pid):
    return next(p for p in products if p["id"] == pid)


# ── 场景 F/I：消息格式 / 顶部统计 / 拆分 ──────────────────────────

META = {
    "status": "success", "target_date": "2026-10-20", "data_date": DATA_DATE,
    "success_categories": [f"c{i}" for i in range(39)],
    "expected_categories": [f"c{i}" for i in range(40)],
    "failed_categories": ["PACK"], "total_clean_rows": 450,
}


@pytest.fixture()
def signed_env(tmp_path, monkeypatch):
    key = tmp_path / "dl_sign.key"
    key.write_text("k" * 32, encoding="utf-8")
    monkeypatch.setenv("FILE_DL_KEY", str(key))
    monkeypatch.setenv("FILE_HOST", "http://t.example")


def _message(db_path, products, signed_env, meta=None):
    return bd.render_dingtalk_message(meta or META, _report(db_path, products))


def test_message_top_summary_no_mysql(db_path, products, signed_env):
    texts = _message(db_path, products, signed_env)
    assert len(texts) == 1
    msg = texts[0]
    assert "【采集概况】" in msg
    assert "**采集结果**：39/40 类别成功" in msg
    assert "**全量数据**：450 条" in msg
    assert "**失败类目**：PACK" in msg
    assert "【40 种重点业务产品报价】" in msg
    assert "目标产品：40 种 ｜ 本次有效新报价：1 种 ｜ 本次无新报价：39 种" in msg
    # 场景 I：绝不出现 MySQL/新增/更新技术统计
    for banned in ("MySQL", "新增", "更新"):
        assert banned not in msg


def test_message_group_order_and_coverage(db_path, products, signed_env):
    texts = _message(db_path, products, signed_env)
    msg = texts[0]
    order = ["一、基础金属（3）", "二、金属（2）", "三、金属盐（4）", "四、废极片（2）",
             "五、黑粉（7）", "六、正极材料（16）", "七、新电芯（6）"]
    pos = -1
    for head in order:
        i = msg.index(head)
        assert i > pos, head
        pos = i
    assert msg.count("- **") == 40  # 40 产品全覆盖


def test_message_missing_line_formats(db_path, products, signed_env):
    msg = _message(db_path, products, signed_env)[0]
    assert "**1#电解铜**：本次无新报价 ｜ 最近有效报价 78000 元/吨（2026-10-17）" in msg
    assert "本次无新报价，历史暂无有效报价" in msg
    assert "**电池级碳酸锂（Li₂CO₃≥99.5%）**：最低 155500" in msg
    assert "涨跌 -200" in msg


def test_message_download_links_signed(db_path, products, signed_env):
    msg = _message(db_path, products, signed_env)[0]
    assert "【数据下载】" in msg
    assert "[下载今日40种业务产品报价](http://t.example/dl/" in msg
    assert "[下载当日全量采集数据](http://t.example/dl/" in msg


def test_message_data_date_note_when_different(db_path, products, signed_env):
    meta = dict(META, data_date="2026-10-17")
    msg = bd.render_dingtalk_message(meta, _report(db_path, products))[0]
    assert "**日期**：2026-10-20（页面数据日期 2026-10-17）" in msg


def test_message_split_covers_all_products(db_path, products, signed_env, monkeypatch):
    monkeypatch.setattr(bd, "MAX_MESSAGE_CHARS", 500)
    texts = bd.render_dingtalk_message(META, _report(db_path, products))
    assert len(texts) > 1
    joined = "\n".join(texts)
    assert joined.count("- **") == 40  # 零丢失、零重复
    assert texts[0].startswith("（1/")
    assert texts[-1].startswith(f"（{len(texts)}/{len(texts)}）")
    assert "【采集概况】" in texts[0]
    assert "【数据下载】" in texts[-1]
    # 分组标题跨消息出现且只出现一次、顺序不变
    heads = ["一、基础金属", "二、金属", "三、金属盐", "四、废极片",
             "五、黑粉", "六、正极材料", "七、新电芯"]
    pos = -1
    for h in heads:
        assert joined.count(h) == 1, h
        i = joined.index(h)
        assert i > pos, h
        pos = i


def test_message_fallback_without_rows(signed_env):
    texts = bd.render_dingtalk_message(META, [])
    assert len(texts) == 1
    assert "【40 种重点业务产品报价】" not in texts[0]
    assert "【采集概况】" in texts[0]


# ── 场景 H：Excel 完整性 ─────────────────────────────

def test_excel_40_rows_and_status(tmp_path, db_path, products):
    path = bd.export_quotes_xlsx(_report(db_path, products), tmp_path, date(2026, 10, 20))
    assert path.name == "SMM锂电业务产品报价_2026-10-20.xlsx"
    assert "每日汇总" in str(path)
    wb = load_workbook(path)
    ws = wb["40种业务产品报价"]
    data_rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert len(data_rows) == 40
    header = [c.value for c in ws[1]]
    assert header == bd.EXCEL_HEADERS
    by_name = {(r[1], r[2] or ""): r for r in data_rows}
    # 新报价行：价格四列有值、状态正确、最近报价为空
    new_row = by_name[("电池级碳酸锂", "Li₂CO₃≥99.5%")]
    assert new_row[3:7] == ("155500", "156500", "156000", "-200")
    assert new_row[8] == DATA_DATE
    assert new_row[9] == "本次有效新报价"
    assert new_row[10] is None and new_row[11] is None
    # 无新报价行：价格四列留空、最近有效报价带真实日期、状态明确
    miss_row = by_name[("A00铝", "")]
    assert all(v in ("", None) for v in miss_row[3:7])
    assert miss_row[7] == "元/吨"
    assert miss_row[9] == "本次无新报价（附最近有效报价）"
    assert miss_row[10] == "20840"
    assert miss_row[11] == "2026-10-17"
    # 无历史行：状态明确，价格全部留空
    none_row = by_name[("修复型磷酸铁锂", "")]
    assert none_row[9] == "本次无新报价，历史暂无有效报价"
    assert all(v in ("", None) for v in none_row[3:7])
