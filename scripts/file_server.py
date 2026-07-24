"""启动文件服务器，供局域网内下载 Excel 文件。"""
import http.server, socketserver, os, sys
from pathlib import Path

PORT = int(os.getenv("FILE_SERVER_PORT", "8888"))
ROOT = Path(__file__).resolve().parents[1] / "data" / "exports"
os.chdir(str(ROOT))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

if __name__ == "__main__":
    with socketserver.ThreadingTCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"文件服务器已启动: http://0.0.0.0:{PORT}")
        print(f"根目录: {ROOT}")
        httpd.serve_forever()
