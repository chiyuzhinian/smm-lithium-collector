from __future__ import annotations
import json, re
from urllib.parse import urlsplit, urlunsplit
from datetime import datetime
from pathlib import Path

PRICE_KEYS=re.compile(r"price|low|high|avg|product|品名|价格|最低|最高|均价",re.I)
SENSITIVE_QUERY=re.compile(r"(?:token|auth|authorization|cookie|password|secret|session|key)",re.I)

def safe_url(url: str) -> str:
 """Never persist query strings because they may contain login credentials/tokens."""
 parts=urlsplit(url)
 return urlunsplit((parts.scheme,parts.netloc,parts.path,"",""))
class NetworkCapture:
 def __init__(self,out_dir:Path): self.out_dir=out_dir; self.candidates=[]; self.pending=set()
 def attach(self,page):
  page.on("response",lambda response:self._schedule(response))
 def _schedule(self,response):
  import asyncio
  task=asyncio.create_task(self._handle(response)); self.pending.add(task); task.add_done_callback(self.pending.discard)
 async def _handle(self,response):
  try:
   ctype=(await response.all_headers()).get("content-type","")
   if "json" not in ctype.lower() or response.request.resource_type not in ("xhr","fetch"): return
   payload=await response.json(); sample=json.dumps(payload,ensure_ascii=False)[:20000]
   if not PRICE_KEYS.search(sample): return
   self.out_dir.mkdir(parents=True,exist_ok=True)
   path=self.out_dir/f"response_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
   redacted=safe_url(response.url)
   path.write_text(json.dumps({"url":redacted,"status":response.status,"data":payload},ensure_ascii=False,indent=2),encoding="utf-8")
   self.candidates.append({"url":redacted,"status":response.status,"path":str(path)})
  except Exception: pass  # Non-JSON/closed responses are expected; never log headers.
 async def drain(self):
  if self.pending:
   import asyncio
   await asyncio.gather(*list(self.pending),return_exceptions=True)
