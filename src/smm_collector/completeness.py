"""completeness — Anchor products + Rolling baseline 检查（Phase E1）。

设计原则：
- **不**硬编码「必须有 400/500/600 行」——数据量随业务变化（PACK 分类时有时无等）。
- 选取 3-5 个**长期稳定**的 anchor products（每次都应有），检查它们是否出现。
- Rolling baseline：从历史 N 天采集数据中算出 p20/p80 周期，过短/过长 → WARN。

Anchor 产品选择规则：
  - 出现频次 ≥ 95%（历史 30 天）
  - 来自核心分类（上游锂矿/中游正极/下游电芯）
  - 与业务核心产品对应（不在 SMM 实验性分类）

业务侧配置：``config/categories_portal.yaml`` 的 ``key_products`` 段（已经存在）；
或这里直接读项目内置常量，避免 YAML 依赖。
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("smm_collector.completeness")

# ── 内置 Anchor Products ─────────────────────────────────────
# 选择依据：历史数据出现频次高 + 业务核心 + 与分类强绑定。
# 任何调整都必须同步更新业务侧 ``business_products.yaml`` 与此处。
# 测试 fixture 也引用此常量。
ANCHOR_PRODUCTS: tuple[tuple[str, str, str], ...] = (
    # (category, product_name, specification)
    ("锂辉石精矿", "锂辉石精矿（6%品位）", "CIF中国"),
    ("碳酸锂", "电池级碳酸锂", "≥99.5%"),
    ("氢氧化锂", "电池级氢氧化锂", "≥56.5%"),
    ("三元材料", "三元材料（523）", "动力型"),
    ("磷酸铁锂", "磷酸铁锂", "动力型"),
)


@dataclass
class AnchorCheckResult:
    present: list[tuple[str, str, str]]
    missing: list[tuple[str, str, str]]
    ratio: float  # present / total

    @property
    def is_ok(self) -> bool:
        # 全部 anchor 出现才算 OK；缺失任意 → 标记待人工审视
        return len(self.missing) == 0


def check_anchor_products(
    rows: Iterable[dict[str, Any]],
    anchors: tuple[tuple[str, str, str], ...] = ANCHOR_PRODUCTS,
) -> AnchorCheckResult:
    """检查 anchor products 是否在采集结果中出现。

    Args:
        rows: 已 validate 的行列表（每行含 ``category`` / ``product_name`` / ``specification``）。
        anchors: 三元组集合（category, product_name, specification）。

    Returns:
        ``AnchorCheckResult``：包含 present / missing / ratio。
    """
    seen = set()
    for r in rows:
        cat = r.get("category") or ""
        prod = r.get("product_name") or ""
        spec = r.get("specification") or ""
        if not cat:
            continue
        seen.add((cat, prod, spec))

    present = [a for a in anchors if a in seen]
    missing = [a for a in anchors if a not in seen]
    ratio = len(present) / len(anchors) if anchors else 1.0
    return AnchorCheckResult(present=present, missing=missing, ratio=ratio)


# ── Rolling baseline ─────────────────────────────────────────


@dataclass
class BaselineResult:
    """基于历史 N 天采集数据量的基线判定。"""

    days_sampled: int
    rows_per_day: list[int]
    p20: int
    p80: int
    median: int
    today_rows: int
    status: str  # "ok" / "warn_short" / "warn_long" / "unknown"
    detail: str


def compute_baseline(
    rows_per_day: list[int],
    today_rows: int,
    *,
    warn_short_ratio: float = 0.5,
    warn_long_ratio: float = 2.0,
    min_history: int = 5,
) -> BaselineResult:
    """计算 rolling baseline 并判定 today_rows 是否在合理区间。

    Args:
        rows_per_day: 历史 N 天每天采集行数（不含今日）。
        today_rows: 今日采集行数。
        warn_short_ratio: today < p20 * ratio → warn_short。
        warn_long_ratio: today > p80 * ratio → warn_long（一般不太可能，警示解析/分类错误）。
        min_history: 历史样本少于该值时退化为 unknown。

    Returns:
        ``BaselineResult``：含各分位数 + 状态 + 原因。
    """
    if len(rows_per_day) < min_history:
        return BaselineResult(
            days_sampled=len(rows_per_day),
            rows_per_day=list(rows_per_day),
            p20=0, p80=0, median=0,
            today_rows=today_rows,
            status="unknown",
            detail=f"history < {min_history} days, baseline unavailable",
        )

    sorted_rows = sorted(rows_per_day)
    n = len(sorted_rows)
    p20 = sorted_rows[int(n * 0.2)]
    p80 = sorted_rows[int(n * 0.8)] if n > 1 else sorted_rows[-1]
    median = sorted_rows[n // 2]

    short_threshold = max(p20 * warn_short_ratio, 1)
    long_threshold = p80 * warn_long_ratio

    if today_rows < short_threshold:
        status = "warn_short"
        detail = f"today={today_rows} < p20*ratio={short_threshold:.0f} (history p20={p20}, median={median})"
    elif today_rows > long_threshold:
        status = "warn_long"
        detail = f"today={today_rows} > p80*ratio={long_threshold:.0f} (history p80={p80})"
    else:
        status = "ok"
        detail = f"today={today_rows} in range [p20={p20}, p80={p80}], median={median}"

    return BaselineResult(
        days_sampled=n,
        rows_per_day=list(rows_per_day),
        p20=p20, p80=p80, median=median,
        today_rows=today_rows,
        status=status,
        detail=detail,
    )


# ── DB 集成：拉历史 N 天采集行数 ────────────────────────────────


def fetch_rows_per_day(
    db_path: Path,
    *,
    lookback_days: int = 14,
    end_date: date | None = None,
) -> list[int]:
    """从 SQLite 拉最近 N 天每天的采集行数（不含今日）。

    Args:
        db_path: SQLite 数据库路径。
        lookback_days: 回溯天数（含今日；返回的长度上限 = lookback_days - 1）。
        end_date: 截止日期（默认 = 今日）。
    """
    end = end_date or date.today()
    start = end - timedelta(days=lookback_days)
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as e:
        logger.warning("fetch_rows_per_day: connect failed: %s", e)
        return []
    try:
        cur = conn.cursor()
        # 用 collected_at 日期聚合；过滤 invalid 行（average_price IS NULL）
        rows = cur.execute(
            """
            SELECT date(collected_at) AS d, COUNT(*)
              FROM lithium_spot_prices
             WHERE date(collected_at) >= date(?)
               AND date(collected_at) <  date(?)
               AND average_price IS NOT NULL
             GROUP BY d
             ORDER BY d
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [int(c) for _, c in rows]
    except sqlite3.Error as e:
        logger.warning("fetch_rows_per_day: query failed: %s", e)
        return []
    finally:
        conn.close()