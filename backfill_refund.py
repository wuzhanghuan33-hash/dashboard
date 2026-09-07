#!/usr/bin/env python3
"""一次性将 1-6 月 refund_amt / post_refund 从飞书补回 data.json

用法（需 CDP Proxy 正在运行）：
  python3 backfill_refund.py

覆盖规则：只填充原来没有 refund_amt 字段的天，已有值的不覆盖
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

FEISHU_URL = "https://vcnrz1ae7b5x.feishu.cn/wiki/LkD9wO05BiZrSkkLYg7czErwn5e"
CDP_PROXY = "http://localhost:3456"
DATA_DIR = Path("/Users/luoxiaomin/Documents/知识库/1-工作/天猫运营/万家乐官旗业绩/dashboard")
DATA_JSON = DATA_DIR / "data.json"

TABS = ["1月", "2-3月", "4月", "5-6月"]

# Known labels in col 2 to search for
LABEL_DATE = "日期"
LABEL_REFUND = "退款金额达成"
LABEL_POST = "去退金额达成"


def excel_serial_to_date(serial):
    s = int(serial)
    if s > 60:
        s -= 1
    return datetime(1899, 12, 31) + timedelta(days=s)


def cdp_new(url, timeout=20):
    r = subprocess.run(["curl", "-s", "-X", "POST", "--data-raw", url,
                        f"{CDP_PROXY}/new"], capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads(r.stdout).get("targetId", "")
    except Exception:
        return ""


def cdp_eval(target_id, js, timeout=30):
    r = subprocess.run(["curl", "-s", "-X", "POST",
                        f"{CDP_PROXY}/eval?target={target_id}",
                        "-d", js], capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads(r.stdout).get("value")
    except Exception:
        return None


def cdp_close(target_id):
    try:
        subprocess.run(["curl", "-s", f"{CDP_PROXY}/close?target={target_id}"],
                       capture_output=True, timeout=5)
    except Exception:
        pass


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


def extract_by_label(target_id):
    """动态按标签找行索引，提取退款/去退数据"""
    js = """
    (function(){
        try {
            var s = window.spread.getActiveSheet();
            var t = s._dataModel.contentModel.variantModel.table;

            // Find row indices by label in col 2 or col 3
            var dateRowIdx = null, refundRowIdx = null, postRefundRowIdx = null;
            Object.keys(t).forEach(function(k) {
                var row = t[k];
                if (!row || !row.data) return;
                // Try both col 2 and col 3 for labels
                [ "2", "3" ].forEach(function(col) {
                    var cell = row.data[col];
                    if (!cell || !cell.value) return;
                    var v = String(cell.value).trim();
                    if (v === "日期" && !dateRowIdx) dateRowIdx = k;
                    else if (v === "退款金额达成" && !refundRowIdx) refundRowIdx = k;
                    else if (v === "去退金额达成" && !postRefundRowIdx) postRefundRowIdx = k;
                });
            });

            if (!dateRowIdx || !refundRowIdx || !postRefundRowIdx) {
                return "MISSING: date=" + dateRowIdx + " refund=" + refundRowIdx + " post=" + postRefundRowIdx;
            }

            // Extract date serials (must be numbers)
            var dateRow = t[dateRowIdx];
            var dates = {};
            for (var c = 3; c < 70; c++) {
                var cell = dateRow.data[String(c)];
                if (cell && cell.value !== undefined && cell.value !== null && typeof cell.value === 'number') {
                    dates[c] = cell.value;
                }
            }

            var refundRow = t[refundRowIdx];
            var postRefundRow = t[postRefundRowIdx];
            var result = {};

            Object.keys(dates).forEach(function(col) {
                var serial = dates[col];
                var refund = null, postRef = null;
                if (refundRow && refundRow.data) {
                    var cell = refundRow.data[col];
                    if (cell && cell.value !== undefined && cell.value !== null) {
                        refund = Math.round(cell.value);
                    }
                }
                if (postRefundRow && postRefundRow.data) {
                    var cell = postRefundRow.data[col];
                    if (cell && cell.value !== undefined && cell.value !== null) {
                        postRef = Math.round(cell.value);
                    }
                }
                if (refund !== null && refund > 0) {
                    result[serial] = {refund_amt: refund, post_refund: postRef || 0};
                }
            });
            return JSON.stringify(result);
        } catch(e) { return 'ERROR: ' + e.message; }
    })()
    """
    raw = cdp_eval(target_id, js)
    if not raw:
        return None, "无返回"
    if raw.startswith("MISSING:") or raw.startswith("ERROR:"):
        return None, raw[:100]
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as e:
        return None, str(e)


def main():
    data = json.loads(DATA_JSON.read_text(encoding="utf-8"))

    tid = cdp_new(FEISHU_URL)
    if not tid:
        print("打开飞书页面失败")
        sys.exit(1)
    time.sleep(5)

    total_filled = 0
    for tab in TABS:
        print(f"======== {tab} ========")
        result = activate_tab(tid, tab)
        if result != "ACTIVATED":
            print(f"  激活失败: {result}")
            continue
        time.sleep(15)

        refund_data, err = extract_by_label(tid)
        if err:
            print(f"  提取失败: {err}")
            continue
        if not refund_data:
            print(f"  无数据")
            continue

        filled = 0
        for serial_str, vals in refund_data.items():
            dt = excel_serial_to_date(int(serial_str))
            month = str(dt.month)
            day = dt.strftime("%d")
            if month not in data["months"]:
                continue
            for d in data["months"][month]["days"]:
                if d["d"] == day and "refund_amt" not in d:
                    d["refund_amt"] = vals["refund_amt"]
                    d["post_refund"] = vals["post_refund"]
                    filled += 1
                    total_filled += 1
                    break

        print(f"  ✓ 填充 {filled} 天")

    cdp_close(tid)

    if total_filled > 0:
        DATA_JSON.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\n总计填充 {total_filled} 天退款数据")
        # 重算月汇总（refund_amt 不影响 actual/summary，但重新保存即可）
    else:
        print("\n无变更（可能已有 refund_amt 字段）")


if __name__ == "__main__":
    main()
