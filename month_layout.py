#!/usr/bin/env python3
"""飞书业绩表各月 tab 结构登记（单一事实源）+ 新月探查。

2026-09-05 依据历次 getValue 实测登记 7/8/9 月的：当期日行区间、日期序列号列、
关键列、2025 段相对当期行的偏移与列映射。verify_data.py 据此对账，
未来接新月（10月）先跑本脚本 probe 探查、再补登记，禁止复用/猜测上月布局。

接新月流程：
  1) python3 month_layout.py probe "10月"   → 打印表头/日期行候选
  2) 对照最近月登记 new 布局（首行/日期列/offset/2025段列）
  3) 更新 MONTH_LAYOUT 与 daily_pull 读取常量；verify_data 自动纳入对账

列均为 0-indexed。单位：金额/人次整数；比率源为小数（data.json 存百分数×100，
转化率存小数）。
"""
import json
import sys
import time
from pathlib import Path

# 当期日行区间 + 关键列（0-indexed）
MONTH_LAYOUT = {
    "7": {
        "tab": "7月",
        "first": 4, "last": 34,          # 2026-07-01..31 逐日一行
        "date": 2,                        # 当期日期 serial
        # 2025 段与当期同一天同一行右侧并排（col37 起），offset 0，同日沿用当期 date 列；
        # 已确认仅列：col41=2025去退金额(=AP)。col39=2025 GMV达成。
        "ly": {"offset": 0, "ly_post_refund": 41},
        "note": "7月当期也支持 getValue 行式(rows4-34)；2025段同行右排",
    },
    "8": {
        "tab": "8月",
        "first": 2, "last": 32,          # 2026-08-01..31，row1=表头
        "date": 3,
        # 2025 段同行（row1 右区 col38..，offset 0，同日沿用当期 date 列）
        "ly": {
            "offset": 0,
            "ly_a": 43, "ly_refund_amt": 47, "ly_post_refund": 49,
            "ly_ref": 52, "ly_v": 58, "ly_b": 64, "ly_conv": 66,
            "ly_aov": 68, "ly_cart_users": 70,
        },
        "note": "当期 AUG_COLS（daily_pull）；2025段 offset0。ly_cart_rate/conv 未探明勿用",
    },
    "9": {
        "tab": "9月",
        "first": 3, "last": 32,          # 2026-09-01..30，row1=月汇总、row2=表头
        "date": 3,
        # 2025 段行式右下区，相对当期日行 offset +2（row5-34），col40=2025日期 serial
        "ly": {
            "offset": 2, "date": 40,
            "ly_a": 42, "ly_refund_amt": 46, "ly_post_refund": 48,
            "ly_ref": 52, "ly_v": 57, "ly_b": 63, "ly_conv": 65,
            "ly_aov": 67, "ly_cart_users": 69, "ly_cart_rate": 71,
            "ly_cart_conv": 73,
        },
        "note": "daily_pull 随读 SEP_LY_COLS 同此；2024段在 col77+ 勿混",
    },
}

# 当期关键值列（对账用）：所有月 col5=业绩达成、col11=去退金额达成（7月除外，7月当期走 extract_july_data）
CUR_VALUE_COLS = {"a": 5, "post_refund": 11}


def probe(tab_name, rows=3, cols=76):
    """开飞书激活指定 tab，打印前几行表头，供人工登记布局。"""
    sys.path.insert(0, str(Path(__file__).parent))
    from daily_pull import ensure_proxy, cdp_new, cdp_eval, cdp_close, FEISHU_URL
    import importlib.util
    spec = importlib.util.spec_from_file_location("blm", str(Path(__file__).parent / "backfill_ly_metrics.py"))
    blm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(blm)

    if not ensure_proxy():
        print("proxy fail"); return 1
    tid = cdp_new(FEISHU_URL)
    time.sleep(8)
    r = blm.activate_tab(tid, tab_name)
    print(f"activate {tab_name}: {r}")
    time.sleep(12)
    js = f"""
    (function(){{
      var s = window.spread.getActiveSheet();
      var o = [];
      for (var r = 1; r <= {rows}; r++) {{
        var cells = [];
        for (var c = 0; c <= {cols}; c++) {{
          var v = s.getValue(r, c);
          if (v !== null && v !== undefined) {{
            var tv = (typeof v === 'number') ? Math.round(v*1e4)/1e4 : String(v);
            cells.push(c + ':' + tv);
          }}
        }}
        o.push('r' + r + ' ' + cells.join(' | '));
      }}
      return o.join('\\n');
    }})()
    """
    out = None
    for i in range(4):
        out = dp_eval = cdp_eval(tid, js, timeout=40)
        if out and ':' in str(out):
            break
        time.sleep(6)
    print(str(out)[:4000])
    cdp_close(tid)
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "probe":
        sys.exit(probe(sys.argv[2]))
    print("usage:")
    print("  python3 month_layout.py probe '<tab名>'   探查某月 tab 表头，登记前先跑")
    print("  python3 -c \"import month_layout; print(json.dumps(month_layout.MONTH_LAYOUT,...))\"")
