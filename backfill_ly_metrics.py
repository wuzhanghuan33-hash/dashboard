#!/usr/bin/env python3
"""从飞书各月 tab 的 2025 段提取全部指标同期值，写入 data.json（新增 ly_* 字段）

对齐规则（与 backfill_yoy_net.py 一致）：读 2025 段"日期"行序列号，
转换为日期后取 month-day，映射到 2026 同月同日。已验证序列号虽为占位年份，
但 month-day 与真实 2025 日期一致（见 2-3月 ly_post_refund 对账）。

单位：ref/cart_rate/cart_conv 存百分数（源为小数×100），conv 存小数，
其余存整数元/人次。

只新增 ly_* 字段，不改动已有字段。
"""
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from daily_pull import ensure_proxy, cdp_new, cdp_eval, cdp_close, FEISHU_URL

DATA_JSON = Path(__file__).parent / "data.json"

# tab 按行序，2025 段是第二个"日期"行所在区间
TABS = ["1月", "2-3月", "4月", "5-6月", "7月", "8月"]

# 指标行标签 → data.json 字段名（各 tab 标签命名不完全一致）
LABEL_MAP = {
    "全店业绩": "a", "GMV达成": "a", "业绩达成": "a",
    "退款金额": "refund_amt", "退款金额达成": "refund_amt",
    "去退金额": "post_refund", "去退金额达成": "post_refund",
    "访客达成": "v",
    "买家达成": "b",
    "转化率达成": "conv",
    "客单价达成": "aov",
    "加购人数达成": "cart_users",
    "加购率达成": "cart_rate",
    "加购转化率达成": "cart_conv",
    "退款率": "ref",
}
# 提取顺序 = 覆盖全部指标
LY_METRICS = ["a", "refund_amt", "post_refund", "v", "b", "conv", "aov",
              "cart_users", "cart_rate", "cart_conv", "ref"]
# 源为小数、data.json 存百分数的字段
PCT_FIELDS = {"ref", "cart_rate", "cart_conv"}


def excel_serial_to_date(serial):
    s = int(serial)
    if s > 60:
        s -= 1
    return datetime(1899, 12, 31) + timedelta(days=s)


def activate_tab(target_id, tab_name):
    js = f"""
    (function(){{
        var tabs = document.querySelectorAll('.tab-list > div');
        var target = null;
        tabs.forEach(function(t){{
            if (t.textContent.trim() === '{tab_name}') target = t;
        }});
        if (!target) return 'NO_TAB';
        var keys = Object.keys(target);
        for (var i = 0; i < keys.length; i++) {{
            if (keys[i].startsWith('__reactEventHandlers')) {{
                var h = target[keys[i]];
                if (h && h.onMouseDown) {{
                    h.onMouseDown({{type:'mousedown', button:0, buttons:1,
                        clientX:0, clientY:0, target:target, currentTarget:target,
                        preventDefault:function(){{}}, stopPropagation:function(){{}}}});
                    return 'ACTIVATED';
                }}
            }}
        }}
        return 'NO_HANDLER';
    }})()
    """
    return cdp_eval(target_id, js)


def build_js():
    labels_json = json.dumps(LABEL_MAP, ensure_ascii=False)
    return f"""
(function(){{
  try {{
    var s = window.spread.getActiveSheet();
    var t = s._dataModel.contentModel.variantModel.table;

    function cellVal(row, c) {{
      var cell = row.data[String(c)];
      var v = cell ? cell.value : null;
      if (v && typeof v === 'object') {{
        if (v.formulaResult && v.formulaResult.value !== undefined) v = v.formulaResult.value;
        else if (v.value !== undefined) v = v.value;
        else v = null;
      }}
      return v;
    }}

    var LABEL_MAP = {labels_json};

    // 1) 所有"日期"行 + 按标签收集指标行
    var dateRows = [];
    var metricRows = {{}};
    Object.keys(t).sort(function(a,b){{return parseInt(a)-parseInt(b)}}).forEach(function(k){{
      var row = t[k];
      if (!row || !row.data) return;
      var labels = [];
      for (var i = 1; i <= 3; i++) {{
        var v = cellVal(row, i);
        if (typeof v === 'string' && v.trim() !== '') labels.push(v.trim());
      }}
      if (labels.indexOf('日期') >= 0) dateRows.push(parseInt(k));
      for (var i = 0; i < labels.length; i++) {{
        var lab = labels[i];
        if (lab.indexOf('目标') >= 0) continue; // 目标行不参与
        if (!metricRows[lab]) metricRows[lab] = [];
        metricRows[lab].push(parseInt(k));
      }}
    }});

    if (dateRows.length < 2) return "MISSING dateRows=" + dateRows.length;
    var d2025 = dateRows[1];                     // 第二个日期行 = 2025 段
    var upper = dateRows.length >= 3 ? dateRows[2] : 9999;

    // 2) 各指标取 2025 段内匹配行，逐列配对日期序列号与指标值
    var out = {{}};
    Object.keys(LABEL_MAP).forEach(function(lab){{
      var rows = (metricRows[lab] || []).filter(function(r){{ return r > d2025 && r < upper; }});
      if (!rows.length) return;
      var mrow = rows[rows.length - 1];
      var mkey = LABEL_MAP[lab];
      var vals = {{}};
      for (var c = 2; c <= 90; c++) {{
        var serial = cellVal(t[String(d2025)], c);
        if (typeof serial === 'number' && serial > 44000 && serial < 48000) {{
          var v = cellVal(t[String(mrow)], c);
          if (typeof v === 'number' && !isNaN(v)) vals[String(Math.round(serial))] = v;
        }}
      }}
      if (Object.keys(vals).length) {{
        if (!out[mkey]) out[mkey] = {{}};
        Object.keys(vals).forEach(function(k){{ out[mkey][k] = vals[k]; }});
      }}
    }});

    return JSON.stringify({{d2025: d2025, upper: upper, out: out}});
  }} catch(e) {{ return 'ERROR:' + e.message; }}
}})()
"""


def extract_tab(tid, tab):
    r = activate_tab(tid, tab)
    if r != "ACTIVATED":
        print(f"  [{tab}] 激活失败: {r}")
        return None
    time.sleep(10)
    raw = cdp_eval(tid, build_js(), timeout=30)
    if not raw:
        print(f"  [{tab}] 无返回")
        return None
    if raw.startswith("MISSING") or raw.startswith("ERROR"):
        print(f"  [{tab}] {raw}")
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  [{tab}] JSON解析失败: {e} raw={raw[:120]}")
        return None


def main():
    if not ensure_proxy():
        print("PROXY_FAIL")
        sys.exit(1)
    tid = cdp_new(FEISHU_URL)
    if not tid:
        print("OPEN_FAIL")
        sys.exit(1)
    time.sleep(6)

    # metric -> {month-day: value}
    ly_data = {}
    for tab in TABS:
        print(f"======== {tab} ========")
        res = extract_tab(tid, tab)
        if not res:
            continue
        n_metrics = 0
        for mkey, serials in res["out"].items():
            if mkey not in ly_data:
                ly_data[mkey] = {}
            cnt = 0
            for serial_str, v in serials.items():
                dt = excel_serial_to_date(int(serial_str))
                key = f"{dt.month}-{dt.strftime('%d')}"
                if key not in ly_data[mkey]:
                    ly_data[mkey][key] = v
                    cnt += 1
            n_metrics += 1
            print(f"  {mkey}: +{cnt}天 (首序列号{list(serials.keys())[0] if serials else '-'})")
        print(f"  共 {n_metrics} 个指标 (d2025={res['d2025']}, upper={res['upper']})")

    cdp_close(tid)

    if not ly_data:
        print("无数据，中止")
        return

    # 应用到 data.json
    data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    applied = {m: 0 for m in LY_METRICS}
    for month_key, month_data in data["months"].items():
        for d in month_data["days"]:
            key = f"{month_key}-{d['d']}"
            for mkey in LY_METRICS:
                src = ly_data.get(mkey, {})
                if key in src:
                    v = src[key]
                    if mkey in PCT_FIELDS:
                        v = round(v * 100, 1)
                    elif mkey == "conv":
                        v = round(v, 4)
                    else:
                        v = round(v)
                    d[f"ly_{mkey}"] = v
                    applied[mkey] += 1

    DATA_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("\n已写入 ly_* 字段（按指标计天）:")
    for mkey in LY_METRICS:
        print(f"  ly_{mkey}: {applied[mkey]} 天")
    print(f"\ndata.json 已更新 ({DATA_JSON})")


if __name__ == "__main__":
    main()
