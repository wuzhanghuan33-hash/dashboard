#!/usr/bin/env python3
"""导出 9226 买家浏览器全部 cookie 到持久文件（headless 重启后注入恢复登录态）。

用法:
  python3 buyer_save_cookies.py
  输出 → ~/.local/share/dashboard/buyer_cookies.json
"""
import json
import subprocess
import sys

PORT = 9226
DEFAULT_PATH = "/Users/luoxiaomin/.local/share/dashboard/buyer_cookies.json"


def get_all_cookies():
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

    cookies = get_all_cookies()
    if not cookies:
        print("✗ 无法读取 9226 cookie（浏览器未启动或无 page 目标）")
        return 1

    with open(out_path, "w") as f:
        json.dump(cookies, f, ensure_ascii=False)
    print(f"✓ 导出 {len(cookies)} 个 cookie → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
