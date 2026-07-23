from __future__ import annotations
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

# ── 可配置阈值 ────────────────────────────────────────────────
PRICE_CHANGE_WARNING = 0.30   # 日间波动超过 30% → warning
PRICE_CHANGE_ERROR = 1.00     # 日间波动超过 100% → error
MAX_PRICE = Decimal("999999999")  # 价格上限（10亿）
MIN_VALID_YEAR = 2000


def validate_row(row: dict, target_date: date | None = None) -> dict:
    """对单行数据执行所有校验规则，返回增加了 validation_status/validation_message 的行。"""
    invalid: list[str] = []
    warnings: list[str] = []

    # 必填字段
    if not row.get("source"):
        invalid.append("source为空")
    if not row.get("market"):
        invalid.append("market为空")
    if not row.get("category"):
        invalid.append("分类为空")
    if not row.get("product_name"):
        invalid.append("品名为空")
    if not row.get("price_date"):
        invalid.append("日期为空或无法解析")
    if not row.get("unit"):
        warnings.append("单位为空")

    # 价格逻辑
    lo = row.get("min_price")
    hi = row.get("max_price")
    avg = row.get("average_price")
    chg = row.get("change_value")

    # 确保是 Decimal 类型
    for key in ("min_price", "max_price", "average_price", "change_value"):
        val = row.get(key)
        if val is not None and not isinstance(val, Decimal):
            try:
                row[key] = Decimal(str(val))
            except Exception:
                row[key] = None

    lo, hi, avg, chg = row.get("min_price"), row.get("max_price"), row.get("average_price"), row.get("change_value")

    # min <= max
    if lo is not None and hi is not None and lo > hi:
        invalid.append("最低价大于最高价")

    # min <= avg <= max (warning only if avg is outside)
    if avg is not None and lo is not None and hi is not None:
        if not (lo <= avg <= hi):
            warnings.append("平均价不在最低价与最高价之间")

    # 价格合理性
    for label, val in [("最低价", lo), ("最高价", hi), ("平均价", avg)]:
        if val is None:
            continue
        if not isinstance(val, Decimal):
            continue
        if val < 0:
            invalid.append(f"{label}为负数")
        elif val > MAX_PRICE:
            warnings.append(f"{label}超过合理上限")

    # change_value 可以为负数，但也检查上限
    if chg is not None and isinstance(chg, Decimal):
        if abs(chg) > MAX_PRICE:
            warnings.append("涨跌值超过合理上限")

    # 日期检查
    pd_val = row.get("price_date")
    if pd_val is not None:
        if isinstance(pd_val, date):
            if pd_val.year < MIN_VALID_YEAR:
                invalid.append(f"价格日期年份异常({pd_val.year})")
        elif isinstance(pd_val, str) and pd_val.strip():
            try:
                parsed = date.fromisoformat(pd_val.strip())
                if parsed.year < MIN_VALID_YEAR:
                    invalid.append(f"价格日期年份异常({parsed.year})")
            except ValueError:
                invalid.append("价格日期格式异常")

    # 日期与目标日期对比
    if target_date and row.get("price_date") and isinstance(row["price_date"], date):
        if row["price_date"] != target_date:
            warnings.append(f"页面日期({row['price_date']})不是目标日期({target_date})")

    status = "invalid" if invalid else "warning" if warnings else "valid"
    row["validation_status"] = status
    row["validation_message"] = "；".join(invalid + warnings)
    return row


def check_price_volatility(
    current_avg: Decimal | None,
    previous_avg: Decimal | None,
    warn_threshold: float = PRICE_CHANGE_WARNING,
    error_threshold: float = PRICE_CHANGE_ERROR,
) -> tuple[str | None, float | None]:
    """检查日间价格波动。

    返回 (level, ratio)：
      - level: None=正常, "warning"=超警告阈值, "error"=超错误阈值
      - ratio: 变化比例（绝对值），无法计算时返回 None
    """
    if current_avg is None or previous_avg is None:
        return None, None
    if not isinstance(current_avg, Decimal):
        current_avg = Decimal(str(current_avg))
    if not isinstance(previous_avg, Decimal):
        previous_avg = Decimal(str(previous_avg))
    if previous_avg == 0:
        return ("warning", None) if current_avg != 0 else (None, None)

    ratio = float(abs((current_avg - previous_avg) / previous_avg))
    if ratio >= error_threshold:
        return "error", ratio
    elif ratio >= warn_threshold:
        return "warning", ratio
    return None, ratio


def run_status(expected: list[str], succeeded: list[str]) -> str:
    """判定采集任务整体状态。"""
    succeeded_set = set(succeeded)
    expected_set = set(expected)
    if expected_set and succeeded_set == expected_set:
        return "success"
    elif succeeded_set:
        return "partial_success"
    return "failed"
