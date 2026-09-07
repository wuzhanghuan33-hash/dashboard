#!/usr/bin/env python3
"""生意参谋历史日度数据补写 → 飞书指定月份业绩表空缺单元格

数据源: portal/coreIndex/new/overview/v3.json (核心指标 API)
  - 支持任意历史 dateRange，返回本店 (self) + 同行均值 (rivalAvg) 日度指标
  - 字段: payAmt/uv/payByrCnt/rfdSucAmt/cartByrCnt/payPct/payRate/netPaymentAmount
  - 与首页「昨日全天」口径一致（8/10 payAmt 740087.90 = 首页 740088 已验证；
    8/1-8/4 与飞书已有值逐格对齐）

用途: 错过定时同步 / 浏览器故障导致某些天没写，事后补写空缺格。
  仅补空缺: 写前逐格 getValue 预检，空/null/0/#DIV! 才写；已有值不动。
  写后 sleep 15s 等飞书自动保存 → 重开新 tab 读回校验。
  目标月/tab/行偏移由 sycm_sync.TARGET_MONTHS 配置（当前: 8月、9月）。

用法:
  python3 sycm_backfill.py                    # 默认补 目标月 1号~昨日
  python3 sycm_backfill.py 9 1 3              # 补 9月1-3日
  python3 sycm_backfill.py 8 7 9              # 补 8月7-9日
  python3 sycm_backfill.py --dry-run 9 1 3    # 只抓取读取，不写入
"""
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from daily_pull import (  # noqa: E402
    ensure_proxy, cdp_new, cdp_close,
    AUG_COLS, FEISHU_URL,
)
# 9225 (生意参谋) 与 9223 (飞书) 的 cdp_eval 签名不同:
#   sycm_extract.cdp_eval(expr)   → 连 9225，只传表达式
#   daily_pull.cdp_eval(target_id, js) → 连 9223
from sycm_extract import cdp_eval as sycm_eval  # noqa: E402
from sycm_sync import (  # noqa: E402
    activate_tab, get_ws, read_cells, write_cells,
    TARGET_MONTHS, FIELDS, get_cell,
)
from sycm_extract import is_logged_in  # noqa: E402
import sycm_risk_check  # noqa: E402

CDP_PORT = 9223

# coreIndex API 字段 → 内部字段名（与 sycm_extract.fetch_daily 返回一致）
# self 里每个指标是 {value, cycleCrc, ...} 结构
CORE_FIELD = {
    "payAmt": "a",
    "rfdSucAmt": "refund_amt",
    "uv": "v",
    "payByrCnt": "b",
    "payRate": "conv",
    "payPct": "aov",
    "cartByrCnt": "cart_users",
}


def get_token():
    """从已加载资源 URL 提取 token（会话内稳定）"""
    out = sycm_eval("(function(){try{var e=performance.getEntriesByType('resource');"
                    "for(var i=0;i<e.length;i++){var m=e[i].name.match(/[?&]token=([0-9a-f]{8,})/);"
                    "if(m)return m[1];}}catch(err){}return null;})()", timeout=15)
    return out


def fetch_core_index(token, month, day):
    """fetch coreIndex overview for a specific day, return self 指标 dict"""
    date_str = f"2026-{int(month):02d}-{int(day):02d}"
    expr = (
        "(async function(){"
        "var u='https://sycm.taobao.com/portal/coreIndex/new/overview/v3.json"
        "?dateType=day&dateRange='+encodeURIComponent('" + date_str + "|" + date_str + "')"
        "+'&sellerType=online&_=1&token=" + token + "';"
        "var r=await fetch(u,{credentials:'include'});"
        "var j=await r.json();"
        "var self=j&&j.content&&j.content.data&&j.content.data.self||{};"
        "var g=function(k){return self[k]&&self[k].value;};"
        "var payAmt=g('payAmt'),uv=g('uv'),payByr=g('payByrCnt'),rfd=g('rfdSucAmt'),"
        "cart=g('cartByrCnt'),pct=g('payPct'),rate=g('payRate'),net=g('netPaymentAmount');"
        "return JSON.stringify({"
        "a:payAmt,v:uv,b:payByr,refund_amt:rfd,cart_users:cart,aov:pct,conv:rate,net_pay:net,"
        "statDate:g('statDate')});"
        "})()"
    )
    raw = sycm_eval(expr, timeout=30)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def build_values(raw):
    """raw 是 coreIndex self 字段 dict → 返回与 FIELDS 对应的写值 dict + 派生字段"""
    a = raw.get("a")
    v = raw.get("v")
    b = raw.get("b")
    refund = raw.get("refund_amt")
    cart = raw.get("cart_users")
    if not a or not v or not b:
        return None
    vals = {}
    vals["a"] = int(round(a))
    vals["refund_amt"] = int(round(refund)) if refund else 0
    vals["post_refund"] = int(round(a - refund)) if refund else int(round(a))
    # ref/cart_rate/cart_conv 按 fetch_daily 口径存百分数，FIELDS 里 /100 转小数
    vals["ref"] = round(refund / a * 100, 1) if refund else 0.0
    vals["v"] = int(round(v))
    vals["b"] = int(round(b))
    vals["conv"] = round(raw.get("conv") or 0.0, 4)
    vals["aov"] = int(round(raw.get("aov"))) if raw.get("aov") else 0
    vals["cart_users"] = int(round(cart)) if cart else 0
    vals["cart_rate"] = round(cart / v * 100, 1) if cart else 0.0
    vals["cart_conv"] = round(b / cart * 100, 1) if cart else 0.0
    return vals


def main():
    dry_run = "--dry-run" in sys.argv

    # 解析参数: [month] [from to]。默认 month = 最近配置月，区间 = 1号~昨日。
    args = [a for a in sys.argv[1:] if a.isdigit()]
    today = datetime.now()
    cfg_month = max(TARGET_MONTHS)  # 当前运营月（最新配置的 tab）
    if len(args) >= 3:
        month, day_from, day_to = int(args[0]), int(args[1]), int(args[2])
    elif len(args) == 1:
        month = int(args[0])
        day_from, day_to = 1, today.day - 1
    else:
        month = cfg_month
        day_from, day_to = 1, today.day - 1
    if month not in TARGET_MONTHS:
        print(f"✗ 未配置 {month}月 写回（当前: {sorted(TARGET_MONTHS)}），跳过。")
        return 1
    cfg = TARGET_MONTHS[month]
    last_day = cfg["last_row"] - cfg["first_row"] + 1

    # 0. 保活 9225
    subprocess.run(
        ["/bin/bash", str(Path(__file__).with_name("sycm_keepalive.sh"))],
        capture_output=True, text=True, timeout=90,
    )

    # 1. 登录态 + 风控
    if not is_logged_in():
        print("✗ 9225 未登录天猫。恢复: bash login_tmall.sh 扫码重登。")
        return 1
    if not sycm_risk_check.check_once():
        print("✗ 检测到风控信号，已熔断中止（详见 sycm_risk.log）")
        return 1

    # 2. 逐日抓取 coreIndex
    token = get_token()
    if not token:
        print("✗ 获取 token 失败")
        return 1
    print(f"token: {token}")
    if day_to > last_day or day_from < 1:
        print(f"✗ {month}月日期范围 1~{last_day} 越界 (from={day_from}, to={day_to})")
        return 1
    plans = {}
    for day in range(day_from, day_to + 1):
        raw = fetch_core_index(token, month, day)
        if not raw or not raw.get("a"):
            print(f"✗ {month}月{day}日 抓取失败/无数据")
            continue
        vals = build_values(raw)
        if not vals:
            print(f"✗ {month}月{day}日 核心指标不全，跳过")
            continue
        plans[day] = (vals, raw)
        print(f"  {month}月{day}日  a={vals['a']:,}  v={vals['v']:,}  b={vals['b']:,}  "
              f"refund={vals['refund_amt']:,}  cart={vals['cart_users']:,}  aov={vals['aov']:,}")

    if not plans:
        print("✗ 无任何一天可补写")
        return 1

    # 3. 飞书环境
    if not ensure_proxy():
        print("✗ CDP Proxy 就绪失败")
        return 1

    tid = cdp_new(FEISHU_URL)
    if not tid:
        print("✗ 打开飞书页面失败")
        return 1
    time.sleep(5)
    res = activate_tab(tid, cfg["tab"])
    if res != "ACTIVATED":
        print(f"✗ 激活 {cfg['tab']} tab 失败: {res}")
        cdp_close(tid)
        return 1
    print("  等待飞书数据加载...")
    time.sleep(15)

    ws = get_ws(CDP_PORT, tid)
    if not ws:
        print("✗ 获取飞书页面 WebSocket 失败")
        cdp_close(tid)
        return 1

    all_written = []   # (day, col, exp, act)
    all_kept = 0
    for day in sorted(plans):
        vals, raw = plans[day]
        row = cfg["first_row"] + (day - 1)
        cells = []
        for field, (col_key, conv) in FIELDS.items():
            v = vals.get(field)
            if v is None:
                continue
            col = cfg["cols"][col_key]
            cells.append((col, conv(v)))
        cols = [c for c, _ in cells]
        cur_map = read_cells(tid, row, cols)
        print(f"\n  row={row} ({month}月{day}日) 待写对照:")
        write_list = []
        for col, val in cells:
            cur = cur_map.get(str(col), "?")
            is_err = isinstance(cur, str) and cur.startswith("#")
            is_empty = cur is None or cur == 0 or cur == "" or cur == "E" or is_err
            kind = "decimal" if isinstance(val, float) else "int"
            marker = "→ 写" if is_empty else "留(已有)"
            disp = "#DIV/0!" if is_err else (f"{cur:.4f}" if isinstance(cur, float) else str(cur))
            print(f"    col{col:<3} 当前={disp:>12}  {marker}  "
                  f"{('%.4f' % val) if isinstance(val,float) else f'{int(val):,}'}")
            if is_empty:
                write_list.append((col, val))
        if dry_run:
            continue
        if not write_list:
            print("  该日无空缺，跳过")
            all_kept += len(cells)
            continue
        result = write_cells(ws, tid, row, write_list)
        for status, c, exp, act in result:
            if status == "W":
                all_written.append((day, c, exp, act))
            elif status == "K":
                all_kept += 1

    if dry_run:
        print("\n(--dry-run) 未写入。")
        cdp_close(tid)
        return 0

    if not all_written:
        print("\n  无空缺单元格需要写入。")
        cdp_close(tid)
        return 0

    # 4. 即时校验 + 持久化校验
    print(f"\n  写回完成: 写入 {len(all_written)} 格, 已有值保留 {all_kept} 格")
    print("  写入后即时校验:")
    immediate_ok = True
    for day, col, exp, act in all_written:
        try:
            match = abs(float(exp) - float(act)) < 0.0005
        except (ValueError, AttributeError):
            match = str(exp) == str(act)
        if not match:
            immediate_ok = False
        print(f"    {day}日 col{col:<3} 期望={exp} 写后={act}  {'✓' if match else '✗ 未生效'}")

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

    print("\n  持久化校验 (重开读回):")
    ok = immediate_ok
    for day, col, exp, _act in all_written:
        row = cfg["first_row"] + (day - 1)
        got = get_cell(tid2, row, col)
        try:
            match = abs(float(exp) - float(got)) < 0.0005
        except (ValueError, AttributeError):
            match = str(exp) == str(got)
        if not match:
            ok = False
        print(f"    {day}日 col{col:<3} 期望={exp} 读回={got}  {'✓' if match else '✗ 未持久化'}")

    cdp_close(tid2)
    if not ok:
        print(f"\n⚠ 校验存在不一致！请人工检查飞书 {cfg['tab']} 表。")
        return 1
    print("\n✓ 补写完成并已持久化。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
