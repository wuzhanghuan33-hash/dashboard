#!/usr/bin/env python3
"""诊断：读7月tab 各行 label(col1-3) + 7/1(col4) 值，定位退款率实际行"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from daily_pull import cdp_new, cdp_close, cdp_eval  # noqa: E402

CDP_PROXY = "http://localhost:3456"

target = cdp_new("https://vcnrz1ae7b5x.feishu.cn/wiki/LkD9wO05BiZrSkkLYg7czErwn5e")
print("target:", target)
time.sleep(8)

js_activate = """
(function(){
    var tabs = document.querySelectorAll('.tab-list > div');
    var target = null;
    tabs.forEach(function(t){
        if (t.textContent.trim() === '7月') target = t;
    });
    if (!target) return 'NO_TAB';
    var keys = Object.keys(target);
    for (var i = 0; i < keys.length; i++) {
        if (keys[i].startsWith('__reactEventHandlers')) {
            var h = target[keys[i]];
            if (h && h.onMouseDown) {
                h.onMouseDown({type:'mousedown', button:0, buttons:1,
                    clientX:0, clientY:0, target:target, currentTarget:target,
                    preventDefault:function(){}, stopPropagation:function(){}});
                return 'ACTIVATED';
            }
        }
    }
    return 'NO_HANDLER';
})()
"""
r = cdp_eval(target, js_activate)
print("activate:", r)
time.sleep(15)

# 读 rows 8-34 的 col1/2/3(label) + col4/5/6(7/1-3)
js = r"""
(function(){
    var s = window.spread.getActiveSheet();
    var t = s._dataModel.contentModel.variantModel.table;
    var out = {};
    for (var r = 8; r <= 34; r++) {
        var row = t[String(r)];
        var arr = [];
        if (row && row.data) {
            for (var c = 1; c <= 6; c++) {
                var cell = row.data[String(c)];
                arr.push(cell && cell.value !== undefined && cell.value !== null ? cell.value : '');
            }
        }
        out[String(r)] = arr;
    }
    return JSON.stringify(out);
})()
"""
raw = cdp_eval(target, js)
print("raw len:", len(raw) if raw else None)
try:
    data = json.loads(raw)
    for r in sorted(data.keys(), key=int):
        print(f"row{r}: {data[r]}")
except Exception as e:
    print("PARSE ERR:", e, raw[:300])

cdp_close(target)
