"""Remove query strings from previously saved diagnostic response URLs."""
import json, sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT=Path(__file__).resolve().parents[1]
def safe_url(url):
    p=urlsplit(url or "")
    return urlunsplit((p.scheme,p.netloc,p.path,"",""))
def main():
    changed=0
    for path in (ROOT/"data/raw/network").glob("*.json"):
        payload=json.loads(path.read_text(encoding="utf-8"))
        clean=safe_url(payload.get("url",""))
        if clean != payload.get("url",""):
            payload["url"]=clean
            path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
            changed += 1
    print(f"sanitized_files={changed}")
if __name__=="__main__": main()
