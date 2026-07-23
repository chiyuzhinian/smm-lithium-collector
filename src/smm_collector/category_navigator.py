from __future__ import annotations
import asyncio, hashlib, re

class CategoryNavigationError(RuntimeError): pass

def _slugify(name: str) -> str:
    """将分类名转为英文蛇形 slug 作为文件/路径标识。"""
    known = {
        "锂金属": "lithium_metal", "锂矿": "lithium_ore", "锂化合物": "lithium_compound",
    }
    if name in known:
        return known[name]
    # 中文名称使用 MD5 短哈希
    if re.search(r'[一-鿿]', name):
        return "cat_" + hashlib.md5(name.encode()).hexdigest()[:8]
    # 英文/数字直接蛇形化
    slug = re.sub(r'[^a-zA-Z0-9]+', '_', name.strip()).strip('_')
    return slug.lower() if slug else "category"

async def discover_categories(page, heading_selector: str) -> list:
    """从页面 DOM 动态发现所有分类（按 DOM 顺序），返回 [{"name":...,"code":...}]。"""
    elements = page.locator(heading_selector)
    names = []
    for i in range(await elements.count()):
        try:
            text = (await elements.nth(i).inner_text()).strip()
            if text and not any(d["name"] == text for d in names):
                names.append({"name": text, "code": _slugify(text)})
        except Exception:
            pass
    return names

async def table_signature(page, selector=None):
 loc=page.locator(selector) if selector else page.locator("table")
 texts=[]
 for i in range(min(await loc.count(),10)):
  try: texts.append((await loc.nth(i).inner_text())[:5000])
  except Exception: pass
 return hashlib.sha256("\n".join(texts).encode()).hexdigest()

class CategoryNavigator:
 def __init__(self,page,categories,selectors,timeout_ms=30000):
  self.page=page; self.categories=categories; self.selectors=selectors; self.timeout_ms=timeout_ms
 async def locate(self,name):
  configured=self.selectors.get("category",{}).get("item")
  if configured:
   loc=self.page.locator(configured).filter(has_text=name)
  else:
   # Exact visible text/role is evidence-based and avoids invented site-specific CSS.
   loc=self.page.get_by_text(name,exact=True)
  visible=[]
  for i in range(await loc.count()):
   if await loc.nth(i).is_visible(): visible.append(loc.nth(i))
  if not visible: raise CategoryNavigationError(f"找不到分类入口：{name}；请运行 inspect_page.py")
  return visible[0]
 async def switch(self,name):
  item=await self.locate(name); selector=self.selectors.get("data",{}).get("table")
  if self.selectors.get("category",{}).get("mode") == "section":
   # The diagnosed SMM page renders every category as a visible heading followed
   # by its table. There is no tab click and therefore no refresh to wait for.
   following=item.locator("xpath=following::table[1]")
   if not await following.count(): raise CategoryNavigationError(f"分类 {name} 后未找到数据表格")
   return True
  before=await table_signature(self.page,selector)
  await item.click(); await self.page.wait_for_timeout(500)
  loading=self.selectors.get("data",{}).get("loading")
  if loading:
   try: await self.page.locator(loading).wait_for(state="hidden",timeout=self.timeout_ms)
   except Exception: pass
  deadline=asyncio.get_running_loop().time()+self.timeout_ms/1000
  changed=False
  while asyncio.get_running_loop().time()<deadline:
   active=await self._is_active(item,name); after=await table_signature(self.page,selector)
   if active and (after!=before or before==hashlib.sha256(b"").hexdigest()): changed=True; break
   await asyncio.sleep(.25)
  if not changed: raise CategoryNavigationError(f"分类 {name} 点击后未确认数据刷新")
  return True
 async def _is_active(self,item,name):
  cfg=self.selectors.get("category",{}); attr=cfg.get("active_attribute")
  if attr: return (await item.get_attribute(attr))==cfg.get("active_value")
  aria=await item.get_attribute("aria-selected")
  classes=(await item.get_attribute("class") or "").lower()
  return aria=="true" or any(x in classes for x in ("active","selected","current")) or (await item.inner_text()).strip()==name
 async def traverse(self,callback,continue_on_failure=True):
  ok={}; failed={}
  for cat in self.categories:
   try: await self.switch(cat["name"]); ok[cat["name"]]=await callback(cat)
   except Exception as exc:
    failed[cat["name"]]=str(exc)
    if not continue_on_failure: raise
  return ok,failed

async def exhaust_page(page,selectors,max_scroll_attempts=30):
 """Expand groups/load-more, traverse normal pagination, and stabilize scroll/virtual lists."""
 more=selectors.get("more_button")
 if more:
  for _ in range(max_scroll_attempts):
   button=page.locator(more); 
   if not await button.count() or not await button.first.is_visible(): break
   await button.first.click(); await page.wait_for_timeout(500)
 scroll_sel=selectors.get("scroll",{}).get("container"); target=page.locator(scroll_sel) if scroll_sel else page.locator("body")
 stable=0; last=-1
 for _ in range(max_scroll_attempts):
  rows=await page.locator(selectors.get("data",{}).get("row") or "table tbody tr").count()
  stable=stable+1 if rows==last else 0; last=rows
  if stable>=3: break
  await target.evaluate("el => { el.scrollTop = el.scrollHeight; window.scrollTo(0, document.body.scrollHeight); }")
  await page.wait_for_timeout(600)
 return last
