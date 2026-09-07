#!/usr/bin/env python3
"""阶段1：验证生意参谋数据可得性 + 校准口径

流程:
  1. 复用 daily_pull 的 ensure_proxy / cdp_new / cdp_eval
  2. 打开生意参谋 URL，检查是否跳登录（登录态是否生效）
  3. dump 页面标题/URL/关键文本，判定数据在 DOM 还是 XHR API
  4. 抓 8/1-8/4 已知值，与 data.json 对账，输出口径映射表

用法:
  python3 probe_sycm.py [URL]
  不传 URL 用默认生意参谋首页 https://sycm.taobao.com/
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from daily_pull import ensure_proxy, cdp_new, cdp_eval, cdp_close, DATA_JSON  # noqa: E402

DEFAULT_URL = "https://sycm.taobao.com/"
# 校准用的已知道数：8/1-8/4（从 data.json 读，不用硬编码）


def page_info(tid):
    js = "JSON.stringify({title: document.title, url: location.href, ready: document.readyState, bodyText: (document.body ? document.body.innerText.slice(0, 500) : '')})"
    raw = cdp_eval(tid, js, timeout=20)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw[:300]}


def is_login_page(info):
    """判断是否跳转到登录页"""
    if not info:
        return True, "无法获取页面信息"
    url = (info.get("url") or "").lower()
    title = (info.get("title") or "").lower()
    text = (info.get("bodyText") or "").lower()
    keywords = ["login", "登录", "扫码", "password", "密码"]
    for k in keywords:
        if k in url or k in title or k in text:
            return True, f"检测到登录特征: '{k}' (url={url[:80]})"
    return False, info.get("url", "")


def dump_xhr_apis(tid):
    """探测页面发起过的 XHR/fetch 请求，看数据走什么接口"""
    js = """
    (function(){
        var out = [];
        var entries = performance.getEntriesByType('resource') || [];
        for (var i = 0; i < entries.length; i++) {
            var name = entries[i].name || '';
            if (/api|report|data|query|sycm|taobao/i.test(name) && name.length < 300) {
                out.push({url: name, type: entries[i].initiatorType || ''});
            }
        }
        return JSON.stringify(out.slice(0, 40));
    })()
    """
    raw = cdp_eval(tid, js, timeout=15)
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def known_days():
    """从 data.json 取 8/1-8/4 已知值作校准基准"""
    try:
        d = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}
    aug = d.get("months", {}).get("8", {})
    known = {}
    for day in aug.get("days", []):
        if day.get("a", 0) > 0:
            known[day["d"]] = {
                "a": day.get("a"),
                "refund_amt": day.get("refund_amt"),
                "post_refund": day.get("post_refund"),
                "v": day.get("v"),
                "b": day.get("b"),
                "aov": day.get("aov"),
                "ref": day.get("ref"),
            }
    return known


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    print(f"=== probe_sycm: {url} ===")

    if not ensure_proxy():
        print("✗ CDP Proxy 就绪失败")
        sys.exit(1)

    tid = cdp_new(url, timeout=25)
    if not tid:
        print("✗ 打开页面失败")
        sys.exit(1)
    print(f"  targetId: {tid}")
    print("  等待页面加载...")
    time.sleep(12)

    # 1. 登录态检查
    info = page_info(tid)
    is_login, reason = is_login_page(info)
    if info:
        print(f"  title: {info.get('title','')[:60]}")
        print(f"  url:   {info.get('url','')[:100]}")
    print(f"  登录态: {'✗ 未登录' if is_login else '✓ 已登录'} ({reason})")

    # 2. dump 页面文本前 800 字
    if info:
        body = (info.get("bodyText") or "").replace("\n", " ")[:800]
        print(f"\n  页面文本前 800 字:\n  {body}")

    # 3. XHR 接口探测
    print("\n  XHR/API 接口 (前 40 条):")
    apis = dump_xhr_apis(tid)
    if apis:
        for a in apis:
            print(f"    [{a['type']}] {a['url'][:130]}")
    else:
        print("    (无 API 资源，数据可能直接在 DOM 或未加载)")

    # 4. 已知值校准
    known = known_days()
    print(f"\n  已知值校准基准 (data.json 8月): {len(known)} 天有实际值")
    for d, vals in sorted(known.items()):
        print(f"    8/{d}: a={vals['a']} refund={vals['refund_amt']} post={vals['post_refund']} "
              f"v={vals['v']} b={vals['b']} aov={vals['aov']} ref={vals['ref']}")

    cdp_close(tid)
    print("\n=== 阶段1 完成。若上面显示『未登录』，请先跑 login_tmall.sh 登录天猫 ===")


if __name__ == "__main__":
    main()
