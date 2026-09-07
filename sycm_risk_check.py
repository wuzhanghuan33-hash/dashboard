#!/usr/bin/env python3
"""生意参谋风控哨兵
检测 9225 窗口页面是否出现反爬/风控信号。命中 → 立即熔断：
  1. 写熔断标记 sycm_RISK.txt（后续 keepalive/sync 见标记即停手）
  2. kill 正在跑的 sycm_sync.py
  3. 关闭 9225 生意参谋 Chrome
  4. 通知用户（通知中心 + 声音 + 弹窗）
检测三层：
  1. URL 关键词（captcha/verify/security 等）
  2. 页面正文关键词（安全验证/滑块/操作频繁等）
  3. 弹窗/通知层警告词（文案动态，覆盖"直接弹警告通知"的情况）
另有提醒级（非熔断）: notify_structure_warning() —— 同步抓取发现核心指标块缺失时
调用，中止写入 + 通知人工查看（防把空/假数据写进业绩表）。
用法:
  python3 sycm_risk_check.py --check     # 检测一次，正常退出0，命中退出1
  python3 sycm_risk_check.py --remind    # 熔断期间的周期性提醒（keepalive 调）
  python3 sycm_risk_check.py --selftest  # 模拟命中一次，验证全链路（会真关9225）
  python3 sycm_risk_check.py --clear     # 清除熔断标记
恢复: 人工确认无风险后删除 sycm_RISK.txt，系统自动恢复。
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
RISK_FLAG = DIR / "sycm_RISK.txt"
RISK_LOG = DIR / "sycm_risk.log"
PORT = 9225
REMIND_INTERVAL = 900  # 熔断期间每 15 分钟重复提醒一次，防止用户没看到首弹

# URL 命中 = 高置信（登录/安全验证跳转页）
URL_RISK_PATTERNS = [
    "captcha", "slider", "verify", "security", "risk", "punish",
    "valid", "blocked", "safeguard", "deny", "limit",
]
# 页面可见文本命中 = 中高置信（正常指标页几乎不可能出现这些词，误报低）
# 注意: 不用宽泛的「存在风险」——生意参谋首页智能诊断文案会写「流量质量存在风险」，
# 是经营建议不是账号风控，会误触发熔断。用精确的「账号存在风险」仍可命中真风控。
TEXT_RISK_PATTERNS = [
    "安全验证", "滑块", "智能验证", "操作过于频繁", "操作频繁",
    "账号异常", "服务繁忙", "已被限制", "风险提示",
    "账号存在风险", "请进行安全验证", "登录已过期",
]
# 弹窗/通知层文本命中 = 中置信。生意参谋风控可能直接弹警告通知（文案动态，
# 关键词无法穷举）→ 检测页面上的 modal/toast/dialog 覆盖层，文本含警告类词即熔断。
# 平衡策略：普通公告（系统维护、活动提醒等）一般不含这些词，不误报。
DIALOG_ALERT_PATTERNS = [
    "警告", "异常", "限制", "违规", "记录", "封禁", "处罚",
    "停止", "暂停", "风险", "检测", "安全", "注意",
]
# 检测弹窗层的 DOM 选择器（含 role/常见类名）
DIALOG_SELECTOR = ('[role="alert"],[role="dialog"],[aria-role="dialog"],'
                   '.modal,.toast,.dialog,[class*="modal"],[class*="toast"],[class*="dialog"]')

# 已知良性浮层放行：生意参谋自家产品引导/营销弹窗（2026-09 起首页常驻
# 「claw-data-analysis」AI 分析助手引导，文案含「异常/风险」会误命中上面的关键词）。
# 结构标记命中即跳过，不做风险熔断。真风控警告不会用产品营销组件弹。
BENIGN_DIALOG_MARKERS = ("claw-data-analysis",)


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(RISK_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def online():
    try:
        r = subprocess.run(["curl", "-s", "-m", "4",
                            f"http://localhost:{PORT}/json/version"],
                           capture_output=True, text=True, timeout=7)
        return bool(r.stdout.strip())
    except Exception:
        return False


def page_snapshot(timeout=15):
    """读 9225 当前页面 URL + title + body 文本 + 可见弹窗层列表。失败返回 None。"""
    expr = json.dumps(
        "(function(){"
        "var t={};"
        "t.url=location.href;"
        "t.title=document.title;"
        "t.body=(document.body&&document.body.innerText||'').slice(0,3000);"
        "t.dialogs=[];"
        "var sel='" + DIALOG_SELECTOR + "';"
        "var els=document.querySelectorAll(sel);"
        "for(var i=0;i<els.length&&t.dialogs.length<6;i++){"
        "  var el=els[i];"
        "  var r=el.getBoundingClientRect();"
        "  if(r.width<=0||r.height<=0)continue;"
        "  var tx=(el.innerText||'').trim();"
        "  if(tx.length>2){"
        "    var sig='';var w=el;for(var k=0;k<4&&w;k++){sig+=(typeof w.className==='string'?w.className:'')+'|';w=w.parentElement;}"
        "    t.dialogs.push({txt:tx.slice(0,200),sig:sig.slice(0,400)});"
        "  }"
        "}"
        "return JSON.stringify(t);})()"
    )
    script = (
        'const http=require("http");'
        'http.get("http://localhost:' + str(PORT) + '/json",res=>{let d="";res.on("data",c=>d+=c);res.on("end",()=>{'
        'const pages=JSON.parse(d).filter(t=>t.type==="page");'
        'const page=pages[0];'
        'if(!page){console.log("NO_PAGE");process.exit(1);}'
        'const sock=new WebSocket(page.webSocketDebuggerUrl);'
        'const timer=setTimeout(()=>{console.log("TIMEOUT");process.exit(1)},' + str(timeout * 1000) + ');'
        'sock.onopen=()=>sock.send(JSON.stringify({id:1,method:"Runtime.evaluate",params:{expression:' + expr + ',returnByValue:true,awaitPromise:true}}));'
        'sock.onmessage=e=>{const m=JSON.parse(e.data);if(m.id===1){clearTimeout(timer);const v=m.result?.result?.value;console.log(typeof v==="string"?v:JSON.stringify(v));sock.close();process.exit(0)}};'
        '})});'
    )
    try:
        r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                           timeout=timeout + 10, cwd="/tmp")
        out = r.stdout.strip()
        if not out or out == "undefined":
            return None
        return json.loads(out)
    except Exception:
        return None


def match_risk(snap):
    """返回 (命中?, 命中项, URL)。三层：URL → 正文 → 弹窗警告。"""
    if not snap:
        return False, None, None
    url = snap.get("url", "")
    body = snap.get("body", "")
    for p in URL_RISK_PATTERNS:
        if re.search(p, url, re.I):
            return True, f"URL:{p}", url
    for p in TEXT_RISK_PATTERNS:
        if p in body:
            return True, f"页面文本:{p}", url
    for dlg in snap.get("dialogs", []):
        # page_snapshot 现返回 {txt, sig}；兼容旧字符串格式
        if isinstance(dlg, dict):
            txt, sig = dlg.get("txt", ""), dlg.get("sig", "")
        else:
            txt, sig = dlg, ""
        # 已知良性产品浮层（结构标记命中）→ 跳过，不放熔断
        if sig and any(m in sig for m in BENIGN_DIALOG_MARKERS):
            continue
        for p in DIALOG_ALERT_PATTERNS:
            if p in txt:
                return True, f"弹窗:{p} 文案:{txt[:40]}", url
    return False, None, url


def notify(title, msg):
    """通知中心+声音+弹窗，全部后台运行不阻塞主流程。"""
    esc = lambda s: s.replace('"', "'")
    subprocess.Popen(["osascript", "-e",
                      f'display notification "{esc(msg)}" with title "{esc(title)}" sound name "Sosumi"'])
    subprocess.Popen(["osascript", "-e",
                      f'display alert "{esc(title)}" message "{esc(msg)}" buttons {{"知道了"}} default button 1'])


def notify_structure_warning():
    """提醒级通知（非熔断）：页面结构异常、核心指标缺失时中止写入并提醒人工查看。
    走通知中心横幅+声音，不用弹窗（避免打扰，也无需手动关闭）。"""
    subprocess.Popen(["osascript", "-e",
                      'display notification "生意参谋页面结构异常（核心指标块缺失），本轮同步已中止。请打开浏览器检查页面状态。" with title "⚠ 生意参谋结构异常" sound name "Glass"'])


def flag_exists():
    return RISK_FLAG.exists()


def write_flag(reason, url):
    data = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "reason": reason, "url": url}
    RISK_FLAG.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def clear_flag():
    if RISK_FLAG.exists():
        RISK_FLAG.unlink()
        log("熔断标记已清除，系统恢复自动运行")


def do_circuit_break(reason, url):
    """执行熔断：标记 + kill sync + 关 9225 + 通知。"""
    log(f"!! 检测到风控信号 [{reason}] url={url}")
    write_flag(reason, url)
    # kill 正在运行的同步进程
    subprocess.run(["pkill", "-f", "sycm_sync.py"], capture_output=True)
    # 关闭 9225 生意参谋 Chrome（只关生意参谋，飞书 9223 不受影响）
    subprocess.run(["pkill", "-f", "chrome-profile-sycm"], capture_output=True)
    notify("⚠ 生意参谋风控触发",
           f"检测到 [{reason}]，已自动停止同步并关闭浏览器。\n处理确认安全后删除 ~/.local/share/dashboard/sycm_RISK.txt 即可恢复。")
    log("熔断完成：已停止同步 + 关闭9225 + 已通知用户")


def check_once():
    """检测一次。正常返回 True，命中触发熔断返回 False。"""
    if not online():
        return True  # 浏览器不在线 → 无可风控
    snap = page_snapshot()
    hit, item, url = match_risk(snap)
    if hit:
        do_circuit_break(item, url)
        return False
    return True


def remind():
    """熔断期间周期性提醒：距上次通知 >15 分钟再弹一次。"""
    if not flag_exists():
        return
    try:
        mtime = RISK_FLAG.stat().st_mtime
    except OSError:
        return
    if time.time() - mtime > REMIND_INTERVAL:
        data = json.loads(RISK_FLAG.read_text(encoding="utf-8"))
        notify("⚠ 生意参谋风控仍熔断中",
               f"原因 [{data.get('reason')}]。确认安全后删除 sycm_RISK.txt 恢复自动运行。")
        RISK_FLAG.touch()  # 更新提醒时间戳


def selftest():
    """模拟命中一次，验证全链路（会真关 9225，之后删标记 + keepalive 自动恢复）。"""
    log("== 自检：模拟风控命中，验证熔断全链路 ==")
    do_circuit_break("SELFTEST-模拟风控", "http://localhost:%d/selftest" % PORT)
    print("已触发熔断。验证：")
    print("  1. 应已弹出系统通知 + 弹窗")
    print("  2. cat ~/.local/share/dashboard/sycm_RISK.txt 存在")
    print("  3. 恢复: rm ~/.local/share/dashboard/sycm_RISK.txt && bash sycm_keepalive.sh")
    return 0


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "--check"
    if arg == "--check":
        sys.exit(0 if check_once() else 1)
    elif arg == "--remind":
        remind()
        sys.exit(0)
    elif arg == "--selftest":
        sys.exit(selftest())
    elif arg == "--clear":
        clear_flag()
        sys.exit(0)
    else:
        print(__doc__)
        sys.exit(2)
