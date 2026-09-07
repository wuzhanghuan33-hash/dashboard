#!/usr/bin/env python3
"""生意参谋「昨日全天」→ 写回飞书当月业绩表空缺单元格

写回通道（已实测持久化）:
  9223 无头 Chrome（飞书登录态）:
    激活对应月份 tab（TARGET_MONTHS 配置）
    对每个空缺单元格: s.setActiveCell(row,col) → CDP 键盘输入值 → Enter
  该路径走飞书真实编辑管线，协同层自动保存。
  （注意: s.setValue 只改内存模型不触发保存，勿用）

写入格式与 daily_pull 读取口径一致:
  a/refund_amt/post_refund/v/b/aov/cart_users → int
  conv                          → 小数 (0.0081)
  ref/cart_rate/cart_conv       → 小数 (0.272 / 0.027 / 0.299)

仅补空缺: 写前逐格 getValue 预检，空/null/0/#DIV! 才写；已有值不动。
写后 sleep 15s 等飞书自动保存 → 重开新 tab 读回校验（只校验实际写入的格）。

用法:
  python3 sycm_sync.py            # 抓取 + 写回 + 校验
  python3 sycm_sync.py --dry-run  # 只抓取读取，不写入
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from daily_pull import (  # noqa: E402
    ensure_proxy, cdp_new, cdp_eval, cdp_close,
    AUG_COLS, SEP_COLS, AUG_FIRST_ROW, AUG_LAST_ROW, FEISHU_URL,
)
from sycm_extract import fetch_daily, is_logged_in  # noqa: E402
import sycm_risk_check  # noqa: E402  风控哨兵：命中即熔断

CDP_PORT = 9223

# 已配置写回的目标月。row 基准 = first_row + (day - 1)。
# 8月: row2-32=1-31日；9月: 飞书该 tab 多一行（row1=月汇总、row2=表头），row3-32=1-30日。
# 列映射必须按月给: 9月从访客列起比 8月左移 1 列（见 daily_pull.SEP_COLS），
# 写回若套用 8月 AUG_COLS 会把 v 写进"买家目标"等错误列。
TARGET_MONTHS = {
    8: {"tab": "8月", "first_row": AUG_FIRST_ROW, "last_row": AUG_LAST_ROW, "cols": AUG_COLS},
    9: {"tab": "9月", "first_row": 3, "last_row": 32, "cols": SEP_COLS},
}

# 字段 → (AUG_COLS 键, 转换函数)。fetch_daily 返回 ref/cart_rate/cart_conv 为百分数(27.2)，
# 飞书存小数(0.272)，与 daily_pull 读取口径 (ref*100) 对齐。
FIELDS = {
    "a":           ("a",           lambda v: int(v)),
    "refund_amt":  ("refund_amt",  lambda v: int(v)),
    "post_refund": ("post_refund", lambda v: int(v)),
    "ref":         ("ref",         lambda v: round(v / 100.0, 4)),
    "v":           ("v",           lambda v: int(v)),
    "b":           ("b",           lambda v: int(v)),
    "conv":        ("conv",        lambda v: v),
    "aov":         ("aov",         lambda v: int(v)),
    "cart_users":  ("cart_users",  lambda v: int(v)),
    "cart_rate":   ("cart_rate",   lambda v: round(v / 100.0, 4)),
    "cart_conv":   ("cart_conv",   lambda v: round(v / 100.0, 4)),
}

JS_ACTIVATE = """
(function(){
    var tabs = document.querySelectorAll('.tab-list > div');
    var target = null;
    tabs.forEach(function(t){
        if (t.textContent.trim() === '%TAB%') target = t;
    });
    if (!target) return 'NO_TAB';
    var keys = Object.keys(target);
    for (var i = 0; i < keys.length; i++) {
        if (keys[i].startsWith('__reactEventHandlers')) {
            var h = target[keys[i]];
            if (h && h.onMouseDown) {
                h.onMouseDown({
                    type:'mousedown', button:0, buttons:1,
                    clientX:0, clientY:0,
                    target:target, currentTarget:target,
                    preventDefault:function(){},
                    stopPropagation:function(){}
                });
                return 'ACTIVATED';
            }
        }
    }
    return 'NO_HANDLER';
})()
"""

# 字符 → CDP 键事件 (code, windowsVirtualKeyCode)。小数/负数用真实键位码。
KEYCODE = {}
for ch in "0123456789":
    KEYCODE[ch] = ("Digit" + ch, ord(ch))
KEYCODE["."] = ("Period", 190)
KEYCODE["-"] = ("Minus", 189)
for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    KEYCODE[ch] = ("Key" + ch, ord(ch))
    KEYCODE[ch.lower()] = ("Key" + ch, ord(ch))


def get_ws(port, target_id):
    """通过 /json 找 page 目标对应的 WebSocketDebuggerUrl"""
    r = subprocess.run(["curl", "-s", "-m", "5", f"http://localhost:{port}/json"],
                       capture_output=True, text=True, timeout=8)
    if not r.stdout:
        return None
    try:
        for t in json.loads(r.stdout):
            if t.get("type") == "page" and t.get("id") == target_id:
                return t.get("webSocketDebuggerUrl")
    except json.JSONDecodeError:
        return None
    return None


def cdp_input(ws, events):
    """发送任意 CDP Input 事件序列: events = [(method, params), ...]"""
    script = (
        'const ws_url=process.argv[1];const evts=JSON.parse(process.argv[2]);'
        'const sock=new WebSocket(ws_url);let id=0;'
        'const send=(method,params)=>new Promise((res)=>{const mid=++id;'
        'const h=(e)=>{const m=JSON.parse(e.data);if(m.id===mid){sock.removeEventListener("message",h);res(m);}};'
        'sock.addEventListener("message",h);sock.send(JSON.stringify({id:mid,method,params}));});'
        'const sleep=ms=>new Promise(r=>setTimeout(r,ms));'
        'sock.onopen=async()=>{for(const ev of evts){await send(ev[0],ev[1]);await sleep(50);}'
        'console.log("DONE");sock.close();process.exit(0);};'
    )
    try:
        r = subprocess.run(["node", "-e", script, ws, json.dumps(events)],
                           capture_output=True, text=True, timeout=60)
        return r.stdout.strip()
    except Exception:
        return "ERR"


def text_events(text):
    evs = []
    for ch in str(text):
        code, vk = KEYCODE.get(ch, ("Key" + ch.upper(), ord(ch.upper())))
        evs.append(("Input.dispatchKeyEvent", {"type": "keyDown", "key": ch, "code": code,
                   "text": ch, "unmodifiedText": ch, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}))
        evs.append(("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch, "code": code,
                   "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}))
    return evs


def enter_events():
    return [
        ("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter",
         "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13, "text": "\r"}),
        ("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter",
         "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}),
    ]


def activate_tab(tid, tab_name):
    """激活指定 tab。飞书页面 React handler 可能延迟就绪（实测 5s 不够、15s 就绪），
    失败自动重试，避免偶发 NO_TAB/NO_HANDLER 中断写入。"""
    for attempt in range(1, 6):
        res = cdp_eval(tid, JS_ACTIVATE.replace("%TAB%", tab_name), timeout=20)
        if res == "ACTIVATED":
            return res
        time.sleep(10)
    return res


def get_cell(tid, row, col):
    raw = cdp_eval(tid, f"(function(){{var s=window.spread.getActiveSheet();"
                         f"try{{var v=s.getValue({int(row)},{int(col)});"
                         f"return JSON.stringify(v===undefined||v===null?null:v);}}catch(e){{return 'E';}}}})()",
                   timeout=20)
    if raw in (None, "E"):
        return "E"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def read_cells(tid, row, cols):
    """读一行指定列，返回 {col_str: value}。异常列标 'E'。"""
    cols_json = json.dumps(sorted(set(int(c) for c in cols)))
    js = (
        "(function(){var s=window.spread.getActiveSheet();var out={};"
        "var cols=" + cols_json + ";"
        "for(var i=0;i<cols.length;i++){var c=cols[i];var v;"
        "try{v=s.getValue(" + str(int(row)) + ",c);}catch(e){v='E';}"
        "out[String(c)]=v===undefined||v===null?null:v;}"
        "return JSON.stringify(out);})()"
    )
    raw = cdp_eval(tid, js, timeout=20)
    if not raw or not raw.startswith("{"):
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def set_active_cell(tid, row, col):
    cdp_eval(tid, f"(function(){{var s=window.spread.getActiveSheet();"
                  f"s.setActiveCell({int(row)},{int(col)});return 'ok';}})()", timeout=20)


def is_empty_val(v):
    """空态判定: null/'' / 0 / #DIV/0! 等错误字符串 → 可写"""
    if v == "E":
        return False  # 读取异常不算空，跳过
    if v is None or v == "":
        return True
    if isinstance(v, str) and v.startswith("#"):
        return True
    if isinstance(v, (int, float)) and v == 0:
        return True
    return False


def write_cells(ws, tid, row, cells):
    """cells: [(col, value)]。逐格: 预检空 → setActiveCell → 键盘输入 → Enter。
    返回 [(status, col, str_value, actual)]:
      W=已写(actual 为写后读回值), K=已有值保留, E=读取异常跳过"""
    out = []
    for col, val in cells:
        cur = get_cell(tid, row, col)
        if cur == "E":
            out.append(("E", col, str(val), "E"))
            continue
        if not is_empty_val(cur):
            out.append(("K", col, str(val), str(cur)))
            continue
        set_active_cell(tid, row, col)
        time.sleep(0.5)
        cdp_input(ws, [("Page.bringToFront", {})] + text_events(str(val)) + enter_events())
        time.sleep(0.5)
        actual = get_cell(tid, row, col)
        out.append(("W", col, str(val), str(actual)))
    return out


def fmt_num(v, kind):
    if v is None or v == "E":
        return "—"
    if kind == "decimal":
        return f"{v:.4f}" if isinstance(v, (int, float)) else str(v)
    return f"{int(v):,}" if isinstance(v, (int, float)) else str(v)


def main():
    dry_run = "--dry-run" in sys.argv

    # 0. 先保活 9225 headless（不在线则自动拉起 + 注入 cookie）
    subprocess.run(
        ["/bin/bash", str(Path(__file__).with_name("sycm_keepalive.sh"))],
        capture_output=True, text=True, timeout=90,
    )

    # 1. 天猫登录态检查（9225 headless 常驻）
    if not is_logged_in():
        print("✗ 9225 未登录天猫（或不在生意参谋页面）。")
        print("  保活/注入失败，可能登录 cookie 过期。恢复: bash login_tmall.sh 扫码重登，再跑 sycm_save_cookies.py。")
        return 1

    # 1.5 风控信号检测：命中则立即熔断（停手+关浏览器+通知）
    if not sycm_risk_check.check_once():
        print("✗ 检测到风控信号，已熔断中止（详见 sycm_risk.log）")
        return 1

    # 2. 抓昨日数据
    data = fetch_daily()
    if not data or not data.get("a"):
        # 核心指标块缺失 → 页面可能被弹窗遮挡/被替换 → 中止写入 + 提醒人工查看
        print("✗ 昨日数据抓取失败（核心指标块缺失，页面结构异常）")
        sycm_risk_check.notify_structure_warning()
        return 1

    y = datetime.now() - timedelta(days=1)
    print(f"昨日: {y.month}月{y.day}日  a={data['a']:,}  v={data['v']:,}  b={data['b']:,}")

    cfg = TARGET_MONTHS.get(y.month)
    if not cfg:
        print(f"  ⚠ 昨日在 {y.month}月，未配置写回 tab（当前: {sorted(TARGET_MONTHS)}），跳过。")
        return 0
    row = cfg["first_row"] + (y.day - 1)
    if row < cfg["first_row"] or row > cfg["last_row"]:
        print(f"  ⚠ 昨日日期超 {y.month}月 范围 (row={row})，跳过。")
        return 0

    # 3. 飞书环境（9223 无头 Chrome）
    if not ensure_proxy():
        print("✗ CDP Proxy 就绪失败")
        return 1

    # 4. 构建写计划（跳过无值字段）
    cells = []
    for field, (col_key, conv) in FIELDS.items():
        v = data.get(field)
        if v is None:
            continue
        col = cfg["cols"][col_key]
        cells.append((col, conv(v)))
    cols = [c for c, _ in cells]

    tid = cdp_new(FEISHU_URL)
    if not tid:
        print("✗ 打开飞书页面失败")
        return 1
    time.sleep(8)

    res = activate_tab(tid, cfg["tab"])
    if res != "ACTIVATED":
        print(f"✗ 激活 {cfg['tab']} tab 失败: {res}")
        cdp_close(tid)
        return 1
    print("  等待飞书数据加载...")
    time.sleep(15)

    # 5. 读当前值，对照待写
    cur_map = read_cells(tid, row, cols)
    print(f"\n  row={row} ({y.month}月{y.day}日) 待写对照:")
    for col, val in cells:
        cur = cur_map.get(str(col), "?")
        is_err = isinstance(cur, str) and cur.startswith("#")
        is_empty = cur is None or cur == 0 or cur == "" or cur == "E" or is_err
        kind = "decimal" if isinstance(val, float) else "int"
        marker = "→ 写" if is_empty else "留(已有)"
        disp = "#DIV/0!" if is_err else fmt_num(cur, 'decimal' if isinstance(cur, float) else 'int')
        print(f"    col{col:<3} 当前={disp:>12}  {marker}  {fmt_num(val, kind)}")

    if dry_run:
        print("\n(--dry-run) 未写入。")
        cdp_close(tid)
        return 0

    # 6. 写回（键盘管线，仅空缺单元格）
    ws = get_ws(CDP_PORT, tid)
    if not ws:
        print("✗ 获取飞书页面 WebSocket 失败")
        cdp_close(tid)
        return 1
    result = write_cells(ws, tid, row, cells)
    if result is None:
        print("✗ 写回失败")
        cdp_close(tid)
        return 1

    written = [(c, exp, act) for status, c, exp, act in result if status == "W"]
    kept = sum(1 for status, _, _, _ in result if status == "K")
    print(f"\n  写回完成: 写入 {len(written)} 格, 已有值保留 {kept} 格")

    if not written:
        print("  无空缺单元格需要写入。")
        cdp_close(tid)
        return 0

    # 立即校验: 写后同 tab 读回 actual 是否等于期望
    print("  写入后即时校验:")
    immediate_ok = True
    for col, exp, act in written:
        try:
            match = abs(float(exp) - float(act)) < 0.0005 if exp.replace("-", "").replace(".", "").isdigit() \
                else str(exp) == str(act)
        except (ValueError, AttributeError):
            match = str(exp) == str(act)
        if not match:
            immediate_ok = False
        print(f"    col{col:<3} 期望={exp} 写后={act}  {'✓' if match else '✗ 未生效'}")

    # 7. 持久化校验: sleep 等自动保存 → 重开新 tab 读回（只校验实际写入的格）
    print("  等待飞书自动保存 (15s)...")
    time.sleep(15)
    cdp_close(tid)

    tid2 = cdp_new(FEISHU_URL)
    if not tid2:
        print("✗ 校验: 重开飞书页面失败")
        return 1
    time.sleep(5)
    res2 = activate_tab(tid2, cfg["tab"])
    if res2 != "ACTIVATED":
        print(f"✗ 校验: 激活 {cfg['tab']} tab 失败: {res2}")
        return 1
    time.sleep(15)

    written_cols = [c for c, _, _ in written]
    read_back = read_cells(tid2, row, written_cols)
    print("\n  持久化校验 (重开读回):")
    ok = immediate_ok
    for col, exp, _act in written:
        got = read_back.get(str(col), "?")
        try:
            match = abs(float(exp) - float(got)) < 0.0005 if str(exp).replace("-", "").replace(".", "").isdigit() \
                else str(exp) == str(got)
        except (ValueError, AttributeError):
            match = str(exp) == str(got)
        if not match:
            ok = False
        print(f"    col{col:<3} 期望={exp} 读回={got}  {'✓' if match else '✗ 未持久化'}")

    cdp_close(tid2)
    if not ok:
        print(f"\n⚠ 校验存在不一致！请人工检查飞书 {cfg['tab']} 表。")
        return 1
    print("\n✓ 写入已持久化。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
