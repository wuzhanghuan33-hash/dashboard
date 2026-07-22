#!/usr/bin/env python3
"""看板本地服务器

启动：  python3 server.py
打开：  http://localhost:8080

每日更新流程：
  1. 编辑 data.json 添加新一天数据
  2. python3 fix_data.py
  3. 刷新浏览器 http://localhost:8080
"""
import http.server
import os
import sys
from datetime import datetime

PORT = 8080
DIR = os.path.dirname(os.path.abspath(__file__))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)
    def do_GET(self):
        # Strip query string for cache busting (?v=xxx)
        if '?' in self.path:
            self.path = self.path.split('?')[0]
        if self.path == '/':
            self.path = '/index.html'
        self.send_cache_headers()
        return super().do_GET()
    def send_cache_headers(self):
        self.cache_control = 'no-cache, no-store, must-revalidate'
    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()
    def log_message(self, fmt, *args):
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] {args[0]} {args[1]} {args[2]}")

try:
    http.server.HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
except OSError:
    print(f"端口 {PORT} 被占用，尝试 {PORT+1}")
    PORT += 1
    http.server.HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
