#!/usr/bin/env python3
"""读取飞书7月tab全部指标，导出 JSON 供复盘分析"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from daily_pull import cdp_new, cdp_close, SHEET7  # noqa: E402

CDP_PROXY = "http://localhost:3456"
HERE = Path(__file__).parent
JS_FILE = HERE / "_july_read.js"

# 打开目标文档
target = cdp_new("https://vcnrz1ae7b5x.feishu.cn/wiki/LkD9wO05BiZrSkkLYg7czErwn5e")


def eval_js(target_id, js):
    """通过文件方式传 JS 给 /eval，避免 curl -d 转义问题"""
    JS_FILE.write_text(js, encoding="utf-8")
    r = subprocess.run(
        ["curl", "-s", "-m", "25", "-X", "POST",
         f"{CDP_PROXY}/eval?target={target_id}",
         "--data-binary", f"@{JS_FILE}"],
        capture_output=True, text=True, timeout=30)
    try:
        return json.loads(r.stdout).get("value")
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


cols_start = SHEET7["data_col_start"]  # 4 = 7月1日
cols_end = SHEET7["data_col_end"]      # 34 = 7月31日
rows = SHEET7["rows"]
ly_section = SHEET7.get("ly_section", {})

row_idx = {k: v["idx"] for k, v in rows.items()}
ly_idx = {k: v["idx"] for k, v in ly_section.items()}

js_parts = [
    "(function(){",
    "  var gv = function(r, c) {",
    "    try {",
    "      var v = window.spread.getSheet(0).getValue(r, c);",
    "      return (v === null || v === undefined) ? '' : v;",
    "    } catch(e) { return ''; }",
    "  };",
    "  var out = {};",
    "  var rowIdx = " + json.dumps(row_idx, ensure_ascii=False) + ";",
    "  var lyIdx = " + json.dumps(ly_idx, ensure_ascii=False) + ";",
    "  for (var k in rowIdx) {",
    "    out[k] = [];",
    "    for (var c = " + str(cols_start) + "; c <= " + str(cols_end) + "; c++) {",
    "      out[k].push(gv(rowIdx[k], c));",
    "    }",
    "  }",
    "  for (var lk in lyIdx) {",
    "    out[lk] = [];",
    "    for (var c = " + str(cols_start) + "; c <= " + str(cols_end) + "; c++) {",
    "      out[lk].push(gv(lyIdx[lk], c));",
    "    }",
    "  }",
    "  return JSON.stringify(out);",
    "})()",
]
js = "\n".join(js_parts)

result = eval_js(target, js)
if result is None:
    print("EVAL FAILED — 可能 spread 对象不可用，或 tab 未激活7月")
    # 尝试诊断
    diag = eval_js(target, "({spread: typeof window.spread, sheets: window.spread ? window.spread.getSheets().map(function(s){return s.name()}) : []})")
    print("DIAG:", diag)
    cdp_close(target)
    sys.exit(1)

try:
    data = json.loads(result)
except Exception as e:
    print("PARSE ERR:", e, "raw:", result[:500])
    cdp_close(target)
    sys.exit(2)

out = HERE / "july_data.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=1)

print("=== 7月数据读取完成 ===")
for k in ["date", "gmv_actual", "gmv_target", "refund_amt", "post_refund", "visitors", "buyers", "aov", "yoy", "ref_rate"]:
    if k in data:
        vals = data[k]
        non_empty = [v for v in vals if v not in ("", None)]
        print(f"{k}: {len(non_empty)}/31 天有值 | head={vals[:3]} | tail={vals[-3:]}")
print("saved to", out)
cdp_close(target)
