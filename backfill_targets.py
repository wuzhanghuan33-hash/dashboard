#!/usr/bin/env python3
"""一次性提取所有月份的各指标目标数据"""
import json, subprocess, sys, time
from datetime import datetime, timedelta
from pathlib import Path

CDP_PROXY = "http://localhost:3456"
FEISHU_URL = "https://vcnrz1ae7b5x.feishu.cn/wiki/LkD9wO05BiZrSkkLYg7czErwn5e"
DATA_DIR = Path.home() / ".local/share/dashboard"

TAB_CONFIGS = {
    "1月": {"refund_amt_t":9,"post_refund_t":11,"visitors_t":20,"buyers_t":26,"conv_t":28,"aov_t":30,"cart_users_t":32,"cart_rate_t":34,"cart_conv_t":36},
    "2-3月":{"refund_amt_t":9,"post_refund_t":11,"visitors_t":20,"buyers_t":22,"conv_t":24,"aov_t":26,"cart_users_t":28,"cart_rate_t":30,"cart_conv_t":32},
    "4月":  {"refund_amt_t":8,"post_refund_t":10,"visitors_t":19,"buyers_t":21,"conv_t":23,"aov_t":25,"cart_users_t":27,"cart_rate_t":29,"cart_conv_t":31},
    "7月":  {"refund_amt_t":9,"post_refund_t":11,"ref_t":15,"visitors_t":20,"buyers_t":22,"conv_t":24,"aov_t":26,"cart_users_t":28,"cart_rate_t":30,"cart_conv_t":32},
}

FIELD_MAP = {"refund_amt_t":"refund_amt_t","post_refund_t":"post_refund_t","ref_t":"ref_t",
             "visitors_t":"v_t","buyers_t":"b_t","conv_t":"conv_t","aov_t":"aov_t",
             "cart_users_t":"cart_users_t","cart_rate_t":"cart_rate_t","cart_conv_t":"cart_conv_t"}

def excel_serial_to_date(serial):
    s=int(serial); return datetime(1899,12,31)+timedelta(days=s-1 if s>60 else s)

def cdp_new(url):
    r=subprocess.run(["curl","-s","-X","POST","--data-raw",url,f"{CDP_PROXY}/new"],capture_output=True,text=True,timeout=20)
    try: return json.loads(r.stdout).get("targetId","")
    except: return ""

def cdp_eval(tid,js,timeout=60):
    r=subprocess.run(["curl","-s","-X","POST",f"{CDP_PROXY}/eval?target={tid}","-d",js],capture_output=True,text=True,timeout=timeout)
    try: return json.loads(r.stdout).get("value")
    except: return None

def activate(tid,name):
    js=f"""
    (function(){{var t=document.querySelectorAll('.tab-list > div');
    for(var i=0;i<t.length;i++){{if(t[i].textContent.trim()==='{name}'){{
    var k=Object.keys(t[i]);for(var j=0;j<k.length;j++){{if(k[j].startsWith('__reactEventHandlers')){{
    var h=t[i][k[j]];if(h&&h.onMouseDown){{h.onMouseDown({{type:'mousedown',button:0,buttons:1,clientX:0,clientY:0,
    target:t[i],currentTarget:t[i],preventDefault:function(){{}},stopPropagation:function(){{}}}});return 'OK';}}}}}}
    return 'NO_HANDLER';}}}}return 'NO_TAB';}})()
    """
    return cdp_eval(tid,js)

def extract_tab(tid,config):
    """提取目标数据。额外提取 rows 0-3 找日期行"""
    all_rows = set(config.values())
    all_rows.update(["0","1","2","3"])  # 找日期
    row_js = "".join(
        f'result["{i}"]=[];try{{var r=t["{i}"];if(r&&r.data){{for(var c=1;c<70;c++){{var cell=r.data[String(c)];if(cell&&cell.value!==undefined&&cell.value!==null)result["{i}"].push(cell.value);else result["{i}"].push(null);}}}}}}catch(e){{}}'
        for i in sorted(all_rows,key=int))
    js=f"""
    (function(){{var s=window.spread.getActiveSheet();var t=s._dataModel.contentModel.variantModel.table;var result={{}};{row_js}return JSON.stringify(result);}})()
    """
    raw=cdp_eval(tid,js)
    if not raw or raw.startswith("ERROR"):
        return None
    try: raw_data=json.loads(raw)
    except: return None

    # 找日期行
    date_row=None
    for ri in ["0","1","2","3"]:
        vals=raw_data.get(ri,[])
        for v in vals[:12]:
            if isinstance(v,(int,float)) and 45000<v<55000:
                date_row=ri; break
        if date_row: break
    if not date_row:
        # fallback: try all
        for ri in sorted(raw_data.keys()):
            vals=raw_data[ri]
            for v in vals[:12]:
                if isinstance(v,(int,float)) and 45000<v<55000:
                    date_row=ri; break
            if date_row: break
    if not date_row:
        print(f"  找不到日期行, keys={list(raw_data.keys())[:10]}")
        return None

    # 找数据起始列
    col_start=None
    for ci,v in enumerate(raw_data[date_row]):
        if isinstance(v,(int,float)) and 45000<v<55000:
            col_start=ci+1; break
    if col_start is None:
        return None
    col_start=max(col_start,4)

    def get_val(ri,ci):
        arr=raw_data.get(ri)
        if not arr or ci-1>=len(arr): return None
        return arr[ci-1]

    days=[]
    for col in range(col_start,col_start+35):
        dv=get_val(date_row,col)
        if not isinstance(dv,(int,float)): continue
        dt=excel_serial_to_date(dv)
        day={"d":dt.strftime("%d")}
        for field,row_idx in config.items():
            val=get_val(str(row_idx),col)
            out=FIELD_MAP.get(field,field)
            if field in ("refund_amt_t","post_refund_t","visitors_t","buyers_t","cart_users_t"):
                day[out]=int(val) if isinstance(val,(int,float)) else 0
            elif field in ("aov_t",):
                day[out]=int(val) if isinstance(val,(int,float)) else 0
            elif field in ("ref_t","cart_rate_t","cart_conv_t"):
                day[out]=round(val*100,1) if isinstance(val,(int,float)) else 0
            elif field=="conv_t":
                day[out]=val if isinstance(val,(int,float)) else 0
        if any(v for k,v in day.items() if k!="d"):
            days.append(day)
    return days

month_key_map={"1月":"1","2-3月":"2","4月":"4","7月":"7"}

data=json.loads((DATA_DIR/"data.json").read_text(encoding="utf-8"))
print("打开飞书...")
tid=cdp_new(FEISHU_URL)
if not tid: print("✗ 失败"); sys.exit(1)
time.sleep(5)

for month_name,config in TAB_CONFIGS.items():
    mkey=month_key_map.get(month_name)
    if mkey not in data["months"]:
        print(f"\n{month_name}: 跳过，不在 data.json")
        continue
    print(f"\n=== {month_name} ===")
    r=activate(tid,month_name)
    if r!="OK": print(f"  激活失败: {r}"); continue
    time.sleep(15)
    days=extract_tab(tid,config)
    if not days:
        print(f"  ✗ 提取为空")
        continue
    print(f"  ✓ {len(days)} 天")
    ed_map={d["d"]:d for d in days}
    for d in data["months"][mkey]["days"]:
        ed=ed_map.get(d["d"])
        if not ed: continue
        for f in ["v_t","b_t","aov_t","ref_t","refund_amt_t","post_refund_t",
                   "conv_t","cart_users_t","cart_rate_t","cart_conv_t"]:
            if ed.get(f) is not None and ed.get(f)!=0:
                d[f]=ed[f]

# 保存
(DATA_DIR/"data.json").write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print("\n✓ 保存完成，生成 data.js...")
subprocess.run([sys.executable,str(DATA_DIR/"fix_data.py")],cwd=str(DATA_DIR))
subprocess.run(["curl","-s",f"{CDP_PROXY}/close?target={tid}"],timeout=5)
