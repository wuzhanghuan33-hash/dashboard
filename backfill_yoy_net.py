#!/usr/bin/env python3
"""从飞书各月 tab 的第二段提取 2025 去退金额，计算剔退后同比

策略：找第二个"日期"行（即 2025 段），读其实际日期序列号，
按同年月日匹配到 2026 数据。
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

TABS = ["1月", "2-3月", "4月", "5-6月", "7月"]


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


JS = """
(function(){
    try {
        var s = window.spread.getActiveSheet();
        var t = s._dataModel.contentModel.variantModel.table;

        // Find ALL "日期" rows (col 2 or 3 label = "日期")
        var dateRows = [];
        var postRefundRows = [];

        Object.keys(t).sort(function(a,b){return parseInt(a)-parseInt(b)}).forEach(function(k) {
            var row = t[k];
            if (!row || !row.data) return;

            var label2 = row.data["2"] ? String(row.data["2"].value || "").trim() : "";
            var label3 = row.data["3"] ? String(row.data["3"].value || "").trim() : "";

            if (label2 === "日期" || label3 === "日期") {
                dateRows.push(parseInt(k));
            }
            if (label2 === "去退金额" || label3 === "去退金额") {
                postRefundRows.push(parseInt(k));
            }
        });

        // Second date row = 2025 section (first is 2026)
        if (dateRows.length < 2 || postRefundRows.length === 0) {
            return "MISSING: dateRows=" + dateRows.length + " postRows=" + postRefundRows.length;
        }

        var d2025row = t[String(dateRows[1])];
        var postRow = t[String(postRefundRows[postRefundRows.length - 1])];

        // Extract date serials from 2025 date row
        var results = {};
        for (var c = 3; c < 70; c++) {
            var cell = d2025row.data[String(c)];
            if (cell && cell.value !== undefined && cell.value !== null && typeof cell.value === 'number') {
                var serial = cell.value;
                var postVal = null;
                if (postRow && postRow.data) {
                    var pc = postRow.data[String(c)];
                    if (pc && pc.value !== undefined && pc.value !== null) {
                        postVal = Math.round(pc.value);
                    }
                }
                if (postVal !== null && postVal > 0) {
                    results[serial] = postVal;
                }
            }
        }
        return JSON.stringify(results);
    } catch(e) { return 'ERROR: ' + e.message; }
})()
"""


def main():
    data = json.loads(DATA_JSON.read_text(encoding="utf-8"))

    tid = cdp_new(FEISHU_URL)
    if not tid:
        print("打开飞书页面失败")
        sys.exit(1)
    time.sleep(5)

    # {month-day: ly_post_refund}
    ly_data = {}

    for tab in TABS:
        print(f"======== {tab} ========")
        result = activate_tab(tid, tab)
        if result != "ACTIVATED":
            print(f"  激活失败: {result}")
            continue
        time.sleep(15)

        raw = cdp_eval(tid, JS)
        if not raw:
            print("  无返回")
            continue
        if raw.startswith("MISSING:") or raw.startswith("ERROR:"):
            print(f"  {raw}")
            continue

        try:
            post_refund_data = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"  JSON解析失败: {e}")
            continue

        n = len(post_refund_data)
        if n == 0:
            print("  无数据")
            continue

        filled = 0
        for serial_str, post_val in post_refund_data.items():
            dt = excel_serial_to_date(int(serial_str))
            # Map to same month+day (aligning 2025 dates to 2026)
            month = str(dt.month)
            day = dt.strftime("%d")
            key = f"{month}-{day}"
            if key not in ly_data:
                ly_data[key] = post_val
                filled += 1

        sample_dt = excel_serial_to_date(int(list(post_refund_data.keys())[0]))
        print(f"  ✓ {n} 天数据，入库 {filled} 天（首日期 {sample_dt.date()}）")

    cdp_close(tid)

    if not ly_data:
        print("无数据变更")
        return

    # Apply to data.json
    applied = 0
    for month_key, month_data in data["months"].items():
        for d in month_data["days"]:
            key = f"{month_key}-{d['d']}"
            if key in ly_data:
                ly_post = ly_data[key]
                d["ly_post_refund"] = ly_post
                curr_post = d.get("post_refund", 0)
                if curr_post > 0 and ly_post > 0:
                    d["y_net"] = round((curr_post - ly_post) / ly_post * 100, 1)
                else:
                    d["y_net"] = None
                applied += 1

    DATA_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Summary
    print(f"\n已更新 {applied} 天的 ly_post_refund 和 y_net")
    print()
    for m in ["1","2","3","4","5","6","7"]:
        days = data["months"][m]["days"]
        n = sum(1 for d in days if d.get("y_net") is not None)
        avg = sum(d["y_net"] for d in days if d.get("y_net")) / n if n else 0
        total_post = sum(d.get("post_refund",0) for d in days)
        total_ly = sum(d.get("ly_post_refund",0) for d in days)
        print(f"  {m}月: {n}天有剔退同比, 均值 {avg:.1f}%, 26去退={total_post/10000:.0f}万, 25去退={total_ly/10000:.0f}万")


if __name__ == "__main__":
    main()
