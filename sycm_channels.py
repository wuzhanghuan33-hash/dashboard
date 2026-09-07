#!/usr/bin/env python3
"""拉取生意参谋 7月 流量来源渠道结构（shopFlowSourceTop），解析渠道树存盘"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sycm_extract  # noqa: E402

TOKEN = "3dd8911c2"
HERE = Path(__file__).parent


def fetch_channels(date_from="2026-07-01", date_to="2026-07-31"):
    expr = (
        "(async function(){try{"
        "var u='https://sycm.taobao.com/flow/overview/live/shopFlowSourceTop/v4.json"
        "?dateRange='+encodeURIComponent('" + date_from + "|" + date_to + "')"
        "+'&dateType=day&pageSize=30&page=1&order=desc&orderBy=uv&device=2"
        "&flowBizType=all&pageType=all&indexCode=uv%2CpayOrderByrCnt%2CpayRate&_=1&token=" + TOKEN + "';"
        "var r=await fetch(u,{credentials:'include'});var j=await r.json();"
        "return JSON.stringify(j);"
        "}catch(e){return JSON.stringify({err:e.message});}})()"
    )
    raw = sycm_extract.cdp_eval(expr, timeout=30)
    return json.loads(raw) if raw else None


def parse_tree(nodes, level, parent, out):
    """递归解析渠道树 → 扁平记录。每个节点可能是分组(有children)或叶子。"""
    for n in nodes or []:
        page = (n.get("pageName") or {}).get("value", "")
        uv = (n.get("uv") or {}).get("value")
        pay_byr = (n.get("payOrderByrCnt") or {}).get("value")
        pay_rate = (n.get("payRate") or {}).get("value")
        item_id = (n.get("pageId") or {}).get("value", "")
        children = n.get("children")
        is_group = bool(children)
        out.append({
            "level": level,
            "parent": parent,
            "page": page,
            "page_id": item_id,
            "is_group": is_group,
            "uv": uv,
            "payByr": pay_byr,
            "payRate": pay_rate,
        })
        if children:
            parse_tree(children, level + 1, page, out)


def main():
    j = fetch_channels()
    if not j or "err" in j:
        print("FAILED:", j)
        return 1
    data = j.get("data", {}).get("data", [])
    rows = []
    parse_tree(data, 1, "root", rows)
    out = HERE / "channel_data.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"渠道节点: {len(rows)} 个")
    # 打印顶级分组
    for r in rows:
        if r["level"] == 1:
            print(f"  L1 {r['page']}: uv={r['uv']} 买家={r['payByr']} 转化={r['payRate']}")
    print("saved to", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
