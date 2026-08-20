#!/usr/bin/env python3
"""从腾讯文档「2026年天猫官旗各类目目标拆解」提取 燃热/电热/厨电 日粒度目标+达成数据

数据源: https://docs.qq.com/sheet/DQ1RueW5uRmpBc3px (只读分享，无需登录)
读取: CDP 打开页面 → 点击底部 sheet tab 切换激活 → SpreadsheetApp.workbook.worksheetManager
      .getSheetBySheetId(id).getCellDataAtPosition(row, col)
      （腾讯表格对未激活 sheet 懒加载，必须先点击 tab 激活）

输出: qqdocs_category.json — {source, categories: {类目: {header_row, days:[{date,rhythm,platform,t,a,refund_t,refund_a,post_t,post_a}]}}}
      日期转 ISO (2026-01-01)，金额保留原值（达成可能为小数）
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from daily_pull import ensure_proxy, cdp_new, cdp_eval, cdp_close

QQ_URL = "https://docs.qq.com/sheet/DQ1RueW5uRmpBc3px?nlc=1&tab=jedba7"

# sheetId → (类目名, 表头行, 同比列映射 {ly_a: 去年同期业绩列, ly_post: 去年同期扣退列, scale: 单位倍率}, 是否只取总块)
# 燃热 AE/AF=支付金额/扣退金额; 电热 AD/AE=支付金额/扣退金额(AF=退款金额勿用); 厨电 AH/AI=GMV/扣退款(万元, scale=10000)
# 厨电是「厨电总/烟机/灶具」三块平行结构，只取总块（c0 块头 == "厨电"），避免烟机/灶具被重复求和
SHEETS = [
    ("jedba7", "燃热", 1, {"ly_a": 30, "ly_post": 31, "scale": 1}, False),
    ("xb8nyq", "电热", 1, {"ly_a": 29, "ly_post": 30, "scale": 1}, False),
    ("lz1i3g", "厨电", 2, {"ly_a": 33, "ly_post": 34, "scale": 1}, True),  # 厨电同比列 1-7月万元/8月元, 生成时智能换算
]

# 需要的列（按表头名查找，取该表头行）
WANT_COLS = [
    "平台节奏", "星期", "日期", "业绩目标", "业绩达成",
    "退款金额目标", "退款金额达成", "去退金额目标", "去退金额达成",
    "访客目标", "访客达成", "买家目标", "买家达成", "客单价目标", "客单价达成",
]

CV_FN = r"""function cv(v){
  if(v===null||v===undefined)return null;
  if(typeof v==='string'||typeof v==='number')return v;
  if(typeof v==='object'){
    var val=v.value;
    if(val===undefined||val===null)return null;
    if(typeof val==='string'||typeof val==='number')return val;
    if(typeof val==='object'&&val.formulaResult&&val.formulaResult.value!==undefined)return val.formulaResult.value;
    if(typeof val==='object'&&val.value!==undefined)return val.value;
    return null;
  }
  return String(v);
}
"""


def click_tab(tid, name):
    js = """(function(){
var name='%s';
var all=document.querySelectorAll('.tab-bar-item-title');
for(var i=0;i<all.length;i++){if(all[i].textContent.trim()===name){
  var r=all[i].getBoundingClientRect();var cx=r.x+r.width/2,cy=r.y+r.height/2;
  var node=all[i];
  while(node&&node.tagName!=='BODY'){
    ['mousedown','mouseup','click'].forEach(function(t){
      node.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,button:0,buttons:1,clientX:cx,clientY:cy}));
    });
    node=node.parentElement;
  }
  return 'OK';
}}
return 'NO_TAB';
})()""" % name
    return cdp_eval(tid, js, timeout=20)


def excel_to_iso(serial):
    """Excel 序列号 → ISO 日期 (1900 系统)"""
    if serial is None or isinstance(serial, str):
        return None
    import datetime
    # 序列号 46023 = 2026-01-01
    base = datetime.date(1899, 12, 30)
    return (base + datetime.timedelta(days=int(serial))).isoformat()


def get_col_map(tid, sheet_id, header_row):
    """读表头行，返回 {列名: 列号}"""
    js = ("(function(){" + CV_FN +
          "var wm=window.SpreadsheetApp.workbook.worksheetManager;\n"
          "var s=wm.getSheetBySheetId('" + sheet_id + "');\n"
          "var out={};\n"
          "for(var c=0;c<60;c++){\n"
          "  var v=cv(s.getCellDataAtPosition(" + str(header_row) + ",c));\n"
          "  if(v!==null&&typeof v==='string'&&v!=='')out[v]=c;\n"
          "}\nreturn JSON.stringify(out);})()")
    raw = cdp_eval(tid, js, timeout=25)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


NUM_FIELDS = ["t", "a", "refund_t", "refund_a", "post_t", "post_a",
              "v_t", "v", "b_t", "b", "aov_t", "aov", "ly_a", "ly_post"]


def aggregate_by_date(clean):
    """跨子块按日期聚合: 数字字段求和, 字符串取首个非空。厨电多子块结构必需"""
    by_date = {}
    for rec in clean:
        d = rec["date"]
        if d not in by_date:
            by_date[d] = dict(rec)
            continue
        base = by_date[d]
        for k, v in rec.items():
            if k == "date":
                continue
            if k in NUM_FIELDS:
                bv = base[k]
                if not isinstance(bv, (int, float)):
                    bv = 0 if not isinstance(bv, str) or bv not in ("", "/") else 0
                if isinstance(v, (int, float)):
                    base[k] = bv + v
            elif base[k] in (None, "") and v not in (None, ""):
                base[k] = v
    return list(by_date.values())


def get_total_block_ranges(tid, sheet_id, date_col, c0_col=0):
    """预扫描多块平行 sheet（如厨电），返回总块数据行的 [start, end) 范围列表。

    判定规则：块头行（日期列 == "日期"）的 c0 == "厨电"（精确匹配，不含 烟机/灶具）
    即为总块；其数据行持续到下一个块头行之前。
    """
    js = ("(function(){" + CV_FN +
          "var wm=window.SpreadsheetApp.workbook.worksheetManager;\n"
          "var s=wm.getSheetBySheetId('" + sheet_id + "');\n"
          "var n=s.getRowCount();\n"
          "var dc=" + str(date_col) + ";\n"
          "var out=[];var cur=-1;\n"
          "for(var r=1;r<n;r++){\n"
          "  var v=cv(s.getCellDataAtPosition(r,dc));\n"
          "  if(v==='日期'){\n"
          "    var c0=cv(s.getCellDataAtPosition(r," + str(c0_col) + "));\n"
          "    if(c0==='厨电'){ if(cur>=0)out.push([cur,r]); cur=r+1; }\n"
          "    else if(cur>=0){ out.push([cur,r]); cur=-1; }\n"
          "  }\n"
          "}\n"
          "if(cur>=0)out.push([cur,n]);\n"
          "return JSON.stringify(out);})()")
    raw = cdp_eval(tid, js, timeout=30)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def read_sheet_days(tid, sheet_id, header_row, col_map, yoy_cols=None, total_block_only=False):
    """读取全部数据行 → days 列表。分批读避免单次返回过大"""
    # 找日期列
    date_col = col_map.get("日期")
    if date_col is None:
        return []
    nrow_js = ("(function(){var wm=window.SpreadsheetApp.workbook.worksheetManager;"
               "var s=wm.getSheetBySheetId('" + sheet_id + "');return s.getRowCount();})()")
    nrow = int(cdp_eval(tid, nrow_js, timeout=15) or 0)

    # 厨电总块口径：预扫描总块数据行范围，只保留这些行
    total_ranges = []
    if total_block_only:
        total_ranges = get_total_block_ranges(tid, sheet_id, date_col)
        print(f"  总块数据行范围: {total_ranges[:5]}{'...' if len(total_ranges) > 5 else ''}")

    def in_total_range(r):
        return any(a <= r < b for a, b in total_ranges)

    # 列列表: 需读的列 = 目标列 + 日期列 + 同比列
    col_names = [n for n in WANT_COLS if n in col_map]
    cols = [col_map[n] for n in col_names]
    if yoy_cols:
        # 同比列直接按列号读，列名用 ly_a / ly_post 标记
        col_names += ["ly_a", "ly_post"]
        cols += [yoy_cols["ly_a"], yoy_cols["ly_post"]]
    col_json = json.dumps(cols)

    days = []
    # 分批读。厨电单块 15 列 JSON 过大易截断，用较小批量
    BATCH = 12
    for r0 in range(header_row + 1, nrow, BATCH):
        r1 = min(r0 + BATCH, nrow)
        js = ("(function(){" + CV_FN +
              "var wm=window.SpreadsheetApp.workbook.worksheetManager;\n"
              "var s=wm.getSheetBySheetId('" + sheet_id + "');\n"
              "var cols=" + col_json + ";\n"
              "var names=" + json.dumps(col_names) + ";\n"
              "var out=[];\n"
              "for(var r=" + str(r0) + ";r<" + str(r1) + ";r++){\n"
              "  var dateV=cv(s.getCellDataAtPosition(r," + str(date_col) + "));\n"
              "  if(dateV===null||typeof dateV!=='number'||dateV<43000)continue;\n"
              "  var row={};\n"
              "  for(var i=0;i<cols.length;i++){\n"
              "    var v=cv(s.getCellDataAtPosition(r,cols[i]));\n"
              "    row[names[i]]=v;\n"
              "  }\n"
              "  out.push([r,dateV,row]);\n"
              "}\nreturn JSON.stringify(out);})()")
        raw = cdp_eval(tid, js, timeout=30)
        if not raw:
            continue
        try:
            batch = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for r, dateV, row in batch:
            if total_block_only and not in_total_range(r):
                continue
            days.append({"serial": dateV, "cols": row})

    return days


def main():
    if not ensure_proxy():
        print("PROXY_FAIL"); return 1

    tid = cdp_new(QQ_URL)
    if not tid:
        print("OPEN_FAIL"); return 1
    time.sleep(15)

    result = {}
    # 类目数据量下限（全量应在 200 天以上，低于此判定懒加载未完成需重试）
    MIN_DAYS = {"燃热": 200, "电热": 200, "厨电": 200}
    for sheet_id, name, header_row, yoy_cols, total_block_only in SHEETS:
        col_map = None
        days = []
        for attempt in range(4):
            click_tab(tid, name)
            time.sleep(8)
            col_map = get_col_map(tid, sheet_id, header_row)
            # 电热退款金额达成列表头为「实际退款金额」，别名映射到标准名
            if name == "电热" and "实际退款金额" in col_map and "退款金额达成" not in col_map:
                col_map["退款金额达成"] = col_map["实际退款金额"]
            print(f"{name} ({sheet_id}) 表头列: {list(col_map.keys())[:25]}")
            days = read_sheet_days(tid, sheet_id, header_row, col_map, yoy_cols, total_block_only)
            serials = [d["serial"] for d in days]
            n_dup = len(serials) - len(set(serials))
            print(f"  读取 {len(days)} 天({n_dup}重复) (第{attempt+1}次)")
            if total_block_only and n_dup > 0:
                # 总块每天应只有 1 行。有重复说明懒加载未完成导致块头漏检，
                # 烟机/灶具块混入总块范围被聚合 → a 翻倍 + ly_a 丢失。强制重试。
                print("  ⚠ 总块范围含重复日期(烟机/灶具混入)，懒加载未完成，重试...")
                time.sleep(12)
                continue
            if len(days) >= MIN_DAYS.get(name, 200) and n_dup == 0:
                break
            if n_dup > 0:
                print("  ⚠ 总块范围含重复日期(烟机/灶具混入)，懒加载未完成，重试...")
                time.sleep(12)
            else:
                print("  ⚠ 天数不足，懒加载未完成，重试...")
        if len(days) < MIN_DAYS.get(name, 200):
            print(f"  ✗ {name} 多次重试仍不达标({len(days)}天)，跳过")
        if len(serials) - len(set(serials)) > 0:
            # 总块重复日期仍在 → 说明懒加载始终未完成，本次提取不可信。
            # 中止写入，避免污染 qqdocs_category.json（a 翻倍 + ly_a 丢失）。
            print(f"  ✗ {name} 总块范围重复日期无法消除，本次提取中止，不写入文件")
            return 3

        # 转 ISO + 整理字段
        clean = []
        for d in days:
            cols = d["cols"]
            iso = excel_to_iso(d["serial"])
            if not iso:
                continue
            rec = {
                "date": iso,
                "rhythm": cols.get("平台节奏"),
                "t": cols.get("业绩目标"),
                "a": cols.get("业绩达成"),
                "refund_t": cols.get("退款金额目标"),
                "refund_a": cols.get("退款金额达成"),
                "post_t": cols.get("去退金额目标"),
                "post_a": cols.get("去退金额达成"),
                "v_t": cols.get("访客目标"),
                "v": cols.get("访客达成"),
                "b_t": cols.get("买家目标"),
                "b": cols.get("买家达成"),
                "aov_t": cols.get("客单价目标"),
                "aov": cols.get("客单价达成"),
                "ly_a": cols.get("ly_a"),
                "ly_post": cols.get("ly_post"),
            }
            # 厨电同比列单位为万元，放大到元
            scale = yoy_cols.get("scale", 1)
            if scale != 1:
                for k in ("ly_a", "ly_post"):
                    v = rec[k]
                    if isinstance(v, (int, float)):
                        rec[k] = v * scale
            clean.append(rec)
        clean = aggregate_by_date(clean)
        result[name] = {"header_row": header_row, "days": clean}

    cdp_close(tid)

    out_path = Path(__file__).with_name("qqdocs_category.json")
    out_path.write_text(json.dumps({
        "source": "腾讯文档 2026年天猫官旗各类目目标拆解",
        "url": QQ_URL,
        "extracted": time.strftime("%Y-%m-%d"),
        "categories": result,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n✓ 已写入 {out_path}")
    for name, data in result.items():
        days = data["days"]
        if days:
            print(f"  {name}: {days[0]['date']} ~ {days[-1]['date']} ({len(days)}天)")
            # 抽 3 天样本验证
            for sample in days[:1] + days[len(days)//2:len(days)//2+1] + days[-1:]:
                print(f"    {sample['date']} {sample['rhythm']} t={sample['t']} a={sample['a']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
