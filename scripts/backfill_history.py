"""SMM 锂电现货历史数据回溯采集 — 通过 hq.smm.cn API 拉取最近 30 个交易日数据。

使用方法：
    python scripts/backfill_history.py                  # 回溯全部 40 个分类
    python scripts/backfill_history.py --category 锂化合物  # 仅回溯指定分类
    python scripts/backfill_history.py --dry-run          # 试运行，不写库
"""
from __future__ import annotations
import argparse, asyncio, hashlib, json, sys, re, os, time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from smm_collector.config import load_config
from smm_collector.logger import setup_logging
from smm_collector.database import Database
from smm_collector.exporter import export_daily
from smm_collector.validator import validate_row
from smm_collector.cleaner import parse_price_date
from smm_collector.browser import open_browser, close_browser

HISTORY_API = "https://hq.smm.cn/ajax/spot/history/{product_id}/{start}/{end}"
HQ_PAGE = "https://hq.smm.cn/new-energy/category/{product_id}"


def compute_record_hash(row: dict) -> str:
    keys = ("category", "product_name", "specification", "min_price",
            "max_price", "average_price", "change_value", "unit", "price_date")
    data = []
    for k in keys:
        v = row.get(k)
        if k.endswith("price") or k == "change_value":
            from smm_collector.cleaner import decimal_text
            data.append(decimal_text(v) if v is not None else "")
        else:
            data.append(str(v or ""))
    return hashlib.sha256("\x1f".join(data).encode("utf-8")).hexdigest()


async def browser_fetch(page, url: str) -> dict | None:
    """通过浏览器页面内 fetch 调用 API。"""
    try:
        raw = await page.evaluate("""
            async (url) => {
                const resp = await fetch(url);
                if (!resp.ok) return null;
                return await resp.json();
            }
        """, url)
        return raw
    except Exception:
        return None


async def get_product_name(page, product_id: str) -> str:
    """通过浏览器 fetch 获取 hq 页面标题提取产品名。"""
    url = HQ_PAGE.format(product_id=product_id)
    try:
        html = await page.evaluate("""
            async (url) => {
                const resp = await fetch(url);
                if (!resp.ok) return '';
                return await resp.text();
            }
        """, url)
        if html:
            m = re.search(r'<title>([^_<]+)', html)
            if m:
                title = m.group(1).strip()
                title = re.sub(r'(今日价格|价格走势图|价格查询|现货价格|市场价.*)$', '', title)
                return title
    except Exception:
        pass
    return ""


async def establish_waf_session(page, cfg) -> bool:
    """建立 hq.smm.cn 的 WAF 安全会话，返回是否成功。"""
    # 先访问 hq 分类页面
    await page.goto("https://hq.smm.cn/new-energy/category/201102250059",
                    wait_until="domcontentloaded", timeout=30000)
    title = await page.title()
    print(f"  hq.smm.cn 初始响应: {title}")

    # 等待 WAF 验证完成
    if "WAF" in title:
        print("  检测到 WAF，等待验证完成…")
        for i in range(5):
            await asyncio.sleep(3)
            # 测试 API 是否就绪
            test_url = HISTORY_API.format(
                product_id="202212050001", start="2026-06-23", end="2026-07-23")
            test_result = await browser_fetch(page, test_url)
            if test_result and test_result.get("code") == 0 and test_result.get("data"):
                print(f"  WAF 验证通过（{i+1}次尝试后）")
                return True
            print(f"  第{i+1}次重试…")
        return False
    else:
        await page.wait_for_timeout(3000)
        # 验证
        test_url = HISTORY_API.format(
            product_id="202212050001", start="2026-06-23", end="2026-07-23")
        test_result = await browser_fetch(page, test_url)
        if test_result and test_result.get("data"):
            print("  API 连接正常")
            return True
        return False


def api_to_row(api_item: dict, category: str, product_name: str,
               collected_at: datetime) -> dict:
    """将 API 返回的单条数据转换为数据库行格式。"""
    price_date = parse_price_date(api_item["renew_date"])

    row = {
        "source": "SMM",
        "market": "SMM锂电现货",
        "category": category,
        "product_name": product_name or category,
        "specification": "",
        "min_price": Decimal(str(api_item.get("low", 0))),
        "max_price": Decimal(str(api_item.get("highs", 0))),
        "average_price": Decimal(str(api_item.get("average", 0))),
        "change_value": Decimal(str(api_item.get("vchange", 0))),
        "unit": "",
        "price_date": price_date,
        "collected_at": collected_at,
        "source_url": HQ_PAGE.format(product_id=api_item.get("product_id", "")),
        "collection_method": "API",
        "raw_text": json.dumps(api_item, ensure_ascii=False),
        "extra_fields": json.dumps({
            "vchange_rate": api_item.get("vchange_rate"),
            "point_precision": api_item.get("point_precision"),
            "change_rate_show": api_item.get("change_rate_show"),
        }, ensure_ascii=False),
        "validation_status": "valid",
        "validation_message": "",
    }
    row["record_hash"] = compute_record_hash(row)
    return row


async def backfill(target_category: str | None = None, dry_run: bool = False):
    """主回溯流程。"""
    cfg = load_config(ROOT)
    log = setup_logging(cfg.root)

    # 加载分类→product_id 映射
    mapping_file = cfg.root / "data/raw/inspection/category_product_mapping.json"
    if not mapping_file.exists():
        print("错误：未找到分类映射文件，请先运行 extract_mapping.py")
        return 1

    all_mappings = json.loads(mapping_file.read_text(encoding="utf-8"))
    if target_category:
        all_mappings = [m for m in all_mappings if m["category"] == target_category]
        if not all_mappings:
            print(f"错误：未找到分类 '{target_category}'")
            return 1

    print(f"{'='*60}")
    print(f"SMM 锂电现货历史数据回溯采集")
    print(f"分类数: {len(all_mappings)}")
    print(f"模式: {'试运行(不写库)' if dry_run else '正式采集'}")
    print(f"{'='*60}")

    today = date.today()
    start_str = today.replace(year=today.year - 1).isoformat()
    end_str = today.isoformat()

    # [1] 启动浏览器并建立 WAF 会话
    print("\n[1/3] 启动浏览器并建立安全会话…")
    pw, browser, context = await open_browser(cfg, headed=False, require_state=True)
    page = await context.new_page()

    try:
        if not await establish_waf_session(page, cfg):
            print("  错误：无法建立 WAF 安全会话，请稍后重试或使用 headed 模式")
            return 1

        # [2] 获取产品名 + 拉取历史数据
        print(f"\n[2/3] 获取产品名并拉取历史数据 ({start_str} ~ {end_str})…")
        all_rows = []
        stats_per_category = {}
        product_names: dict[str, str] = {}

        for i, m in enumerate(all_mappings):
            pid = m["product_id"]
            cat = m["category"]

            # 获取产品名（前30个可能被WAF拒绝，使用分类名兜底）
            if i == 0:
                # 第一次尝试获取产品名
                pname = await get_product_name(page, pid)
                product_names[pid] = pname or cat
            else:
                pname = await get_product_name(page, pid)
                product_names[pid] = pname or cat

            # 拉取历史数据（带重试）
            url = HISTORY_API.format(product_id=pid, start=start_str, end=end_str)
            raw = None
            for attempt in range(3):
                raw = await browser_fetch(page, url)
                if raw and raw.get("code") == 0 and raw.get("data"):
                    break
                if attempt < 2:
                    await asyncio.sleep(2)

            if not raw or raw.get("code") != 0 or not raw.get("data"):
                print(f"  [{i+1:2d}/{len(all_mappings)}] {cat:12s} → 无数据")
                stats_per_category[cat] = 0
                await asyncio.sleep(0.3)
                continue

            items = raw["data"]
            now = datetime.now()
            pname = product_names.get(pid, cat)
            rows = [api_to_row(item, cat, pname, now) for item in items]
            validated = [validate_row(r) for r in rows]
            all_rows.extend(validated)

            dates = sorted(set(str(r["price_date"]) for r in validated if r["price_date"]))
            date_range = f"{dates[0]} ~ {dates[-1]}" if dates else "N/A"
            pname_short = pname[:28] if pname != cat else cat
            print(f"  [{i+1:2d}/{len(all_mappings)}] {cat:12s} → {len(validated)}条 ({date_range})  {pname_short}")
            stats_per_category[cat] = len(validated)
            await asyncio.sleep(0.3)

    finally:
        await close_browser(pw, browser)

    # [3] 存储和导出
    print(f"\n[3/3] 存储和导出…")
    print(f"  总数据行: {len(all_rows)}")

    if not all_rows:
        print("  无数据可存储")
        return 1

    success_cats = [c for c, n in stats_per_category.items() if n > 0]
    failed_cats = [c for c, n in stats_per_category.items() if n == 0]

    run_id = f"backfill_{today.isoformat()}_{datetime.now().strftime('%H%M%S')}"
    meta = {
        "run_id": run_id,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "target_date": str(today),
        "expected_categories": [m["category"] for m in all_mappings],
        "discover_mode": "api_backfill",
        "success_categories": success_cats,
        "failed_categories": failed_cats,
        "errors": {},
        "version": "1.0.0",
        "page_url": "hq.smm.cn (API)",
        "page_title": "SMM历史数据API回溯",
        "login_status": "api",
        "status": "success" if len(success_cats) == len(all_mappings) else "partial_success",
        "total_raw_rows": len(all_rows),
        "total_clean_rows": len(all_rows),
    }

    if not dry_run:
        db = Database(cfg.path("database_path"))
        db_stats = db.upsert(all_rows)
        print(f"  数据库: 新增 {db_stats['inserted']} / 更新 {db_stats['updated']} / 重复 {db_stats['duplicate']}")
        db.save_run(meta)

        try:
            xlsx, csv_dir = export_daily(
                all_rows, meta, cfg.path("export_dir"), today,
                db=db,
                rolling_config=cfg.settings.get("rolling_price_export", {}),
            )
            print(f"  导出: {xlsx}")
        except OSError as e:
            print(f"  导出文件错误（数据已入库）: {e}")
        except Exception as e:
            print(f"  导出异常（数据已入库）: {e}")
    else:
        print("  [DRY RUN] 跳过数据库写入和导出")

    print(f"\n完成！成功 {len(success_cats)}/{len(all_mappings)} 个分类，状态: {meta['status']}")
    if failed_cats:
        print(f"失败分类: {', '.join(failed_cats)}")
    return 0 if meta["status"] == "success" else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SMM锂电现货历史数据回溯")
    parser.add_argument("--category", help="仅回溯指定分类")
    parser.add_argument("--dry-run", action="store_true", help="试运行，不写入数据库")
    args = parser.parse_args()
    code = asyncio.run(backfill(args.category, args.dry_run))
    raise SystemExit(code)
