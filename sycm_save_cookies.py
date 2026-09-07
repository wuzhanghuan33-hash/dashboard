#!/usr/bin/env python3
"""导出 9225 浏览器全部 cookie 到持久文件（供 headless 重启后注入恢复登录态）。

用法:
  python3 sycm_save_cookies.py            # 导出到 ~/.local/share/dashboard/sycm_cookies.json
  python3 sycm_save_cookies.py --path X   # 导出到指定路径

只在 9225 已登录（在生意参谋首页）时调用；输出到 stdout 返回写入的 cookie 数。
"""
import json
import subprocess
import sys

PORT = 9225
DEFAULT_PATH = "/Users/luoxiaomin/.local/share/dashboard/sycm_cookies.json"


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

    cookies = get_all_cookies()
    if not cookies:
        print("✗ 无法读取 9225 cookie（浏览器未启动或无 page 目标）")
        return 1

    with open(out_path, "w") as f:
        json.dump(cookies, f, ensure_ascii=False)
    print(f"✓ 导出 {len(cookies)} 个 cookie → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
