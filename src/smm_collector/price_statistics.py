"""近N日价格统计：产品分组、滚动均价计算。

负责：产品分组键生成、有效数据筛选、Decimal 均值计算、有效天数统计。
"""
from __future__ import annotations
from collections import defaultdict
from decimal import Decimal


PRODUCT_KEY_FIELDS = ["source", "market", "category", "product_name", "specification", "unit"]


def product_key(row: dict) -> str:
	"""基于业务唯一键字段生成产品分组键。"""
	parts = [str(row.get(f, "") or "").strip() for f in PRODUCT_KEY_FIELDS]
	return "\x1f".join(parts)


def group_by_product(rows: list[dict]) -> dict[str, list[dict]]:
	"""将行列表按产品分组键分组。"""
	groups = defaultdict(list)
	for r in rows:
		groups[product_key(r)].append(r)
	return dict(groups)


def compute_rolling_average(
	rows: list[dict],
	window_dates: list[str],
	include_warning: bool = True,
	exclude_invalid: bool = True,
) -> tuple[Decimal | None, int]:
	"""为单个产品的多日数据计算近N日均价。

	Args:
		rows: 一个产品的所有日期的行列表
		window_dates: 窗口内的日期列表（按从旧到新排列）
		include_warning: warning 数据是否参与计算
		exclude_invalid: invalid 数据是否排除

	Returns:
		(近N日均价, 有效天数)
	"""
	# 只取窗口日期内、状态可用的行
	valid_prices: list[Decimal] = []
	for r in rows:
		pd_str = str(r.get("price_date", ""))
		if pd_str not in window_dates:
			continue
		status = r.get("validation_status", "valid")
		if status == "invalid" and exclude_invalid:
			continue
		if status == "warning" and not include_warning:
			continue
		avg = r.get("average_price")
		if avg is None:
			continue
		if not isinstance(avg, Decimal):
			try:
				avg = Decimal(str(avg))
			except Exception:
				continue
		valid_prices.append(avg)

	if not valid_prices:
		return None, 0

	total = sum(valid_prices)
	return total / len(valid_prices), len(valid_prices)


def enrich_with_rolling_average(
	rows: list[dict],
	window_dates: list[str],
	config: dict | None = None,
) -> list[dict]:
	"""为所有行增加 近N日均价 和 近N日有效天数 字段。

	同一产品在窗口日期内的所有行获得相同的均价。

	Returns:
		增加了 three_day_average_price 和 three_day_valid_count 的新列表
	"""
	cfg = config or {}
	include_warning = cfg.get("include_warning_records", True)
	exclude_invalid = cfg.get("exclude_invalid_records", True)
	add_count = cfg.get("add_valid_day_count", True)

	groups = group_by_product(rows)
	# 预计算每个产品的均价
	avg_cache: dict[str, tuple[Decimal | None, int]] = {}
	for key, group_rows in groups.items():
		avg_cache[key] = compute_rolling_average(
			group_rows, window_dates, include_warning, exclude_invalid)

	# 为每行添加结果
	result = []
	for r in rows:
		row = dict(r)
		key = product_key(row)
		avg_val, valid_count = avg_cache.get(key, (None, 0))
		row["three_day_average_price"] = avg_val
		if add_count:
			row["three_day_valid_count"] = valid_count
		result.append(row)
	return result


def select_window_dates(all_dates: list[str], window_days: int = 3) -> list[str]:
	"""从全部日期列表中选择最近 N 个日期（按从旧到新排列）。"""
	sorted_dates = sorted(set(all_dates), reverse=True)
	selected = sorted_dates[:window_days]
	return sorted(selected)
