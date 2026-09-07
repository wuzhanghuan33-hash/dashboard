#!/usr/bin/env python3
"""生意参谋数据提取器 — 连 9225 常驻有头 Chrome，抓首页「昨日全天」数据

首页每个指标块有稳定 id（如 overview-index-uv=访客数），
block 内文本格式固定：指标名|今日值|环比|昨日全天|昨日值[...]
据此按 id 提取，派生飞书缺的比率字段。

只依赖：9225 有头 Chrome 的 CDP 端口（常驻，登录态在内存）。
"""
import json
import re
import subprocess

PORT = 9225

# 首页指标块 id → 字段
METRIC_IDS = {
    "payAmt": "top-left-overview-payAmt,cycleCrc",      # 支付金额
    "uv": "overview-index-uv,cycleCrc",                  # 访客数
    "payByr": "overview-index-payByrCnt,cycleCrc",       # 支付买家数
    "payRate": "overview-index-payRate,cycleCrc",        # 支付转化率(小数)
    "rfdAmt": "overview-index-rfdSucAmt,cycleCrc",       # 退款金额(完结)
    "cartByr": "overview-index-cartByrCnt,cycleCrc",     # 加购人数
    "aov": "overview-index-payPct,cycleCrc",             # 客单价
    "netPayAmt": "overview-index-netPaymentAmount,cycleCrc",  # 净支付金额
}


def cdp_eval(expr, timeout=20):
    """通过 node 直连 9225 websocket 执行 JS"""
    expr_json = json.dumps(expr)
    script = (
        'const http=require("http");'
        'http.get("http://localhost:' + str(PORT) + '/json",res=>{let d="";res.on("data",c=>d+=c);res.on("end",()=>{'
        'const pages=JSON.parse(d).filter(t=>t.type==="page");'
        'const page=pages.find(t=>t.url&&t.url.indexOf("sycm.taobao.com")!==-1&&t.url.indexOf("login")===-1)||pages.find(t=>t.url&&t.url.indexOf("login")===-1)||pages[0];'
        'if(!page){console.log("NO_PAGE");process.exit(1);}'
        'const sock=new WebSocket(page.webSocketDebuggerUrl);'
        'const timer=setTimeout(()=>{console.log("TIMEOUT");process.exit(1)},' + str(timeout * 1000) + ');'
        'sock.onopen=()=>sock.send(JSON.stringify({id:1,method:"Runtime.evaluate",params:{expression:' + expr_json + ',returnByValue:true,awaitPromise:true}}));'
        'sock.onmessage=e=>{const m=JSON.parse(e.data);if(m.id===1){clearTimeout(timer);const v=m.result?.result?.value;console.log(typeof v==="string"?v:JSON.stringify(v));sock.close();process.exit(0)}};'
        '})});'
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       timeout=timeout + 10, cwd="/tmp")
    out = r.stdout.strip()
    if not out or out == "undefined":
        return None
    return out


def is_logged_in():
    """检查 9225 窗口当前页面是否登录态（不在登录页）"""
    out = cdp_eval("location.href")
    return bool(out) and "login" not in out


def is_metric_readable():
    """探测核心指标块是否可读。URL 显示已登录但指标读不到 →
    登录态静默失效（页面停在缓存 home.htm，数据接口鉴权已过期）。
    返回 True 表示可读/页面正常，False 表示指标读不到需处理。"""
    try:
        raw = fetch_daily(return_raw=True)
    except Exception:
        return False
    # 有效值特征：至少一个指标返回了数字（'-'/'null'/'None' 都算无效）
    valids = 0
    for v in raw.values():
        if v and v not in ("-", "null", "None"):
            valids += 1
    return valids > 0


def get_yesterday_value(metric_key):
    """提取某指标的「昨日全天」值（原始字符串）"""
    block_id = METRIC_IDS.get(metric_key)
    if not block_id:
        return None
    # 注意: 元素 id 含逗号（如 top-left-overview-payAmt,cycleCrc），
    # querySelector('[id="..."]') 会被 Chrome 当作选择器列表解析而匹配失败，
    # 必须用 getElementById 纯字符串匹配。
    js = (
        "(function(){"
        "var el=document.getElementById('" + block_id + "');"
        "if(!el)return null;"
        "var t=el.innerText.split(String.fromCharCode(10));"
        "var i=t.indexOf('昨日全天');"
        "if(i<0||i+1>=t.length)return null;"
        "return t[i+1];"
        "})()"
    )
    return cdp_eval(js)


def parse_number(s):
    """'883,448' → 883448; '0.81%' → 0.0081; '1,725.48' → 1725.48"""
    if not s:
        return None
    s = s.replace(",", "").strip()
    m = re.match(r"^([-+]?[\d.]+)%?$", s)
    if not m:
        return None
    v = float(m.group(1))
    if s.endswith("%"):
        return v / 100.0
    return v


def fetch_daily(return_raw=False):
    """抓首页「昨日全天」全部指标，返回飞书字段 dict。

    返回: {a, refund_amt, post_refund, v, b, conv, aov, cart_users,
           ref, cart_rate, cart_conv}
    比率字段口径：conv/ref/cart_rate/cart_conv 存百分比数（如 27.2）
    与 data.json 的现有字段一致（conv 例外，data.json 存小数 0.0081，见注）。
    """
    raw = {}
    for key in METRIC_IDS:
        raw[key] = get_yesterday_value(key)

    if return_raw:
        return raw

    a = parse_number(raw.get("payAmt"))
    v = parse_number(raw.get("uv"))
    b = parse_number(raw.get("payByr"))
    conv = parse_number(raw.get("payRate"))
    refund_amt = parse_number(raw.get("rfdAmt"))
    cart_users = parse_number(raw.get("cartByr"))
    aov = parse_number(raw.get("aov"))
    net_pay = parse_number(raw.get("netPayAmt"))

    if not a or not v or not b:
        return None

    # 派生字段
    post_refund = round(a - refund_amt) if refund_amt else None
    ref = round(refund_amt / a * 100, 1) if refund_amt and a else None
    cart_rate = round(cart_users / v * 100, 1) if cart_users and v else None
    cart_conv = round(b / cart_users * 100, 1) if b and cart_users else None

    return {
        "a": int(a),
        "refund_amt": int(refund_amt) if refund_amt else 0,
        "post_refund": int(post_refund) if post_refund else 0,
        "v": int(v),
        "b": int(b),
        "conv": round(conv, 4) if conv else 0.0,   # 小数，匹配 data.json
        "aov": int(aov) if aov else 0,
        "cart_users": int(cart_users) if cart_users else 0,
        "ref": ref,          # 百分比数
        "cart_rate": cart_rate,
        "cart_conv": cart_conv,
        "net_pay": net_pay,  # 验证用
    }


if __name__ == "__main__":
    if not is_logged_in():
        print("未登录或 9225 窗口不在生意参谋页面")
    else:
        data = fetch_daily(return_raw=True)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        print()
        print("=== 派生后 ===")
        print(json.dumps(fetch_daily(), ensure_ascii=False, indent=2))
