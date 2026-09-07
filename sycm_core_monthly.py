#!/usr/bin/env python3
"""拉取 coreIndex 月度聚合完整字段（self + rivalAvg），对比 2026-07 / 2025-07 存盘"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sycm_extract  # noqa: E402

TOKEN = "3dd8911c2"
HERE = Path(__file__).parent

MONTHS = [
    ("m2026_07", "2026-07-01|2026-07-31"),
    ("m2025_07", "2025-07-01|2025-07-31"),
]


def fetch_month(dr):
    expr = (
        "(async function(){try{"
        "var u='https://sycm.taobao.com/portal/coreIndex/new/overview/v3.json"
        "?dateType=month&dateRange='+encodeURIComponent('" + dr + "')"
        "+'&sellerType=online&_=1&token=" + TOKEN + "';"
        "var r=await fetch(u,{credentials:'include'});var j=await r.json();"
        "var d=j.content&&j.content.data;"
        "return JSON.stringify({self:d&&d.self,rival:d&&d.rivalAvg});"
        "}catch(e){return JSON.stringify({err:e.message});}})()"
    )
    raw = sycm_extract.cdp_eval(expr, timeout=30)
    return json.loads(raw) if raw else None


def main():
    out = {}
    for key, dr in MONTHS:
        d = fetch_month(dr)
        if not d or "err" in d:
            print(f"{key} FAILED:", d)
            continue
        out[key] = d
        time.sleep(5)
    if not out:
        return 1
    f = HERE / "core_monthly.json"
    f.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved to", f, "| keys:", list(out.keys()))


if __name__ == "__main__":
    sys.exit(main())
