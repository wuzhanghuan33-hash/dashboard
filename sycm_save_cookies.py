#!/usr/bin/env python3
"""导出 9225 浏览器全部 cookie 到持久文件（供 headless 重启后注入恢复登录态）。

用法:
  python3 sycm_save_cookies.py            # 导出到 ~/.local/share/dashboard/sycm_cookies.json
  python3 sycm_save_cookies.py --path X   # 导出到指定路径

只在 9225 已登录（在生意参谋首页）时调用；输出到 stdout 返回写入的 cookie 数。

守卫（2026-09-18 加）：未登录态导出的快照会覆盖掉可用的恢复票。判据用**当前页面
URL**，不用 cookie 内容——实测未登录态照样导出 96 个 cookie 且含 cookie2/
_tb_token_/sgcookie/unb（访客页也有这些），按 cookie 判据完全拦不住，当天就因此
把一份未登录快照覆盖到了原文件上。页面落在登录域才是可靠信号。
另：覆盖前滚动备份一份 .prev，作为最后退路。
"""
import json
import os
import shutil
import subprocess
import sys

PORT = 9225
DEFAULT_PATH = "/Users/luoxiaomin/.local/share/dashboard/sycm_cookies.json"
# URL 含这些词 = 落在登录/通行证域 → 未登录
LOGIN_URL_MARKERS = ("login", "passport")
MIN_COOKIES = 50


def get_page_url():
    """读 9225 当前页 URL。失败返回 None。"""
    script = (
        'const http=require("http");'
        'http.get("http://localhost:' + str(PORT) + '/json",res=>{let d="";res.on("data",c=>d+=c);res.on("end",()=>{'
        'const page=JSON.parse(d).find(t=>t.type==="page");'
        'if(!page){console.log("NO_PAGE");process.exit(1);}'
        'console.log(page.url||"");process.exit(0);'
        '});});'
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20, cwd="/tmp")
    out = r.stdout.strip()
    return out if out and out != "NO_PAGE" else None


def get_all_cookies():
    """通过 CDP 连 9225 读取全部 cookie（含 HttpOnly）"""
    script = (
        'const http=require("http");'
        'http.get("http://localhost:' + str(PORT) + '/json",res=>{let d="";res.on("data",c=>d+=c);res.on("end",()=>{'
        'const page=JSON.parse(d).find(t=>t.type==="page");'
        'if(!page){console.log("NO_PAGE");process.exit(1);}'
        'const sock=new WebSocket(page.webSocketDebuggerUrl);'
        'let id=0;'
        'const send=(method,params)=>new Promise(resolve=>{const mid=++id;'
        'const h=e=>{const m=JSON.parse(e.data);if(m.id===mid){sock.removeEventListener("message",h);resolve(m);}};'
        'sock.addEventListener("message",h);sock.send(JSON.stringify({id:mid,method,params}));});'
        'sock.onopen=async()=>{'
        'const r=await send("Network.getAllCookies",{});'
        'console.log(JSON.stringify(r.result.cookies||[]));'
        'sock.close();process.exit(0);'
        '};});});'
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, cwd="/tmp")
    out = r.stdout.strip()
    if not out or out == "NO_PAGE":
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def main():
    out_path = DEFAULT_PATH
    if "--path" in sys.argv:
        out_path = sys.argv[sys.argv.index("--path") + 1]

    url = get_page_url()
    if not url:
        print("✗ 读不到 9225 页面 URL（浏览器未启动或无 page 目标）")
        return 1
    if any(m in url.lower() for m in LOGIN_URL_MARKERS):
        print(f"✗ 当前页面是登录页（{url}），未登录态快照会毁掉可用恢复票，"
              f"拒绝覆盖 {out_path}（保留现有快照）")
        return 1

    cookies = get_all_cookies()
    if not cookies:
        print("✗ 无法读取 9225 cookie（浏览器未启动或无 page 目标）")
        return 1
    if len(cookies) < MIN_COOKIES:
        print(f"✗ 只读到 {len(cookies)} 个 cookie（< {MIN_COOKIES}），"
              f"判定异常，拒绝覆盖 {out_path}（保留现有快照）")
        return 1

    if os.path.exists(out_path):
        shutil.copy2(out_path, out_path + ".prev")
    with open(out_path, "w") as f:
        json.dump(cookies, f, ensure_ascii=False)
    print(f"✓ 导出 {len(cookies)} 个 cookie → {out_path}（页面: {url}，旧快照存 .prev）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
