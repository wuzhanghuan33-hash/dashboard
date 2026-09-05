#!/usr/bin/env python3
"""看板数据校验：覆盖率（data.json 自检，零页面）+ 抽样对账（开飞书读源表比 data.json）。

--coverage   每月「有当期实际值却缺同期 ly_*」的天逐月清单；进行中月份（当月有 a>0）任何缺同期 → 告警。
--reconcile  开飞书，按 date serial 独立 getValue 读 7/8/9 关键列（当期 a/post_refund +
             同期 ly_a/ly_post_refund/ly_v/ly_b/ly_conv…），与 data.json 逐天比对，不一致即告警。
失败走 macOS 通知中心+弹窗（同风控哨兵 notify），且返回非 0。

列锚定来自 month_layout.MONTH_LAYOUT（历次实测），不依赖 daily_pull 内部读取函数，
可独立发现「读列错位 / merge 改错 / fix_data 误清」三类问题。
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

DIR = Path(__file__).parent
DATA_JSON = DIR / "data.json"
from month_layout import MONTH_LAYOUT, CUR_VALUE_COLS  # noqa: E402


def notify(title, msg):
    esc = lambda s: s.replace('"', "'")
    subprocess.Popen(["osascript", "-e",
                      f'display notification "{esc(msg)}" with title "{esc(title)}" sound name "Glass"'])
    subprocess.Popen(["osascript", "-e",
                      f'display alert "{esc(title)}" message "{esc(msg)}" buttons {{"知道了"}} default button 1'])


def serial_to_md(serial):
    s = int(serial)
    if s > 60:
        s -= 1
    return datetime(1899, 12, 31) + timedelta(days=s)


# ============ coverage: data.json 自检 ============
# 当期字段 → 对应同期 ly_*（某月登记表声明哪些 ly 列可用才强制）
LY_PAIRS = [("a", "ly_a"), ("refund_amt", "ly_refund_amt"), ("post_refund", "ly_post_refund"),
            ("v", "ly_v"), ("b", "ly_b"), ("conv", "ly_conv"), ("aov", "ly_aov"),
            ("cart_users", "ly_cart_users")]


def coverage_check():
    data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    violations = []   # (month, day, field)
    rows = []
    for mk, m in data["months"].items():
        ly_cfg = MONTH_LAYOUT.get(mk, {}).get("ly", {})
        avail = {lyf for lyf in ly_cfg.keys() if lyf not in ("offset", "date")}
        miss = []
        for d in m["days"]:
            if not d.get("a"):
                continue   # 无实际业绩的天不要求同期
            for cur, lyf in LY_PAIRS:
                if lyf not in avail or cur not in d:
                    continue
                cv = d.get(cur)
                if cv is None or cv == 0:
                    continue   # 当期无实际值 → 不要求同期
                if d.get(lyf) is None:
                    miss.append((d["d"], cur, lyf))
                    violations.append((mk, d["d"], cur, lyf))
        rows.append((mk, sum(1 for x in m["days"] if x.get("a")), miss))
    print("== 覆盖率（当月有实际值却缺同期）==")
    for mk, na, miss in rows:
        if not miss:
            print(f"  {mk}月: {na}天实际值, 缺同期 0 ✓")
            continue
        print(f"  ⚠ {mk}月: {na}天实际值, 缺同期 {len(miss)} 处")
        for d, cur, lyf in miss[:10]:
            print(f"     缺 {d} 当期{cur} 的 {lyf}")
        if len(miss) > 10:
            print(f"     等共 {len(miss)} 处")
    if violations:
        msg = "缺同期: " + ", ".join(f"{m}/{d} {cur}→{lyf}" for m, d, cur, lyf in violations[:5])
        notify("⚠ 数据覆盖率不足", msg + "（看板同比会漏计/漂移）")
        return 1
    print("  ✓ 全部有实际值的天均带同期")
    return 0


# ============ reconcile: 开飞书读源表对账 ============
def _ev(tid, js, tries=4):
    sys.path.insert(0, str(DIR))
    from daily_pull import cdp_eval
    out = None
    for _ in range(tries):
        out = cdp_eval(tid, js, timeout=40)
        if out:
            break
        time.sleep(6)
    return out


def _read_block(tid, layout, key_prefix):
    """按登记读某 tab 当期/同期块：{md: {col: value}}。key_prefix 决定扫当期 or ly 行。
    当期扫 first..last、列 = date + CUR_VALUE_COLS；ly 扫 (first+offset)..(last+offset)、
    列 = date + ly 值列。date 列为 serial，md 由 serial→month-day。"""
    first, last = layout["first"], layout["last"]
    date_col = layout["date"]
    ly_cfg = layout.get("ly", {})
    offset = ly_cfg.get("offset", 0)
    ro_lo = first + offset
    ro_hi = last + offset
    ro_date = date_col if offset == 0 else ly_cfg["date"]
    if key_prefix == "ly":
        val_cols = {ly_cfg[c] for c in ly_cfg.keys() if c not in ("offset", "date")}
        date_c, lo, hi = ro_date, ro_lo, ro_hi
    else:
        val_cols = set(CUR_VALUE_COLS.values())
        date_c, lo, hi = date_col, first, last
    js = f"""
    (function(){{
      var s = window.spread.getActiveSheet();
      var o = [];
      for (var r = {lo}; r <= {hi}; r++) {{
        var sdate = s.getValue(r, {date_c});
        if (typeof sdate !== 'number') continue;
        var row = {{r: r, serial: Math.round(sdate*1e4)/1e4}};
        [{(",".join(str(c) for c in sorted(val_cols)))}].forEach(function(c){{
          var v = s.getValue(r, c);
          row['c'+c] = (typeof v === 'number') ? Math.round(v*1e4)/1e4 : v;
        }});
        o.push(row);
      }}
      return JSON.stringify(o);
    }})()
    """
    raw = _ev(tid, js)
    out = {}
    if not raw or not raw.startswith("["):
        return out, raw
    for row in json.loads(raw):
        try:
            md = serial_to_md(row["serial"])
            out[f"{md.month}-{md.strftime('%d')}"] = row
        except Exception:
            continue
    return out, None


def reconcile():
    sys.path.insert(0, str(DIR))
    from daily_pull import ensure_proxy, cdp_new, cdp_close, FEISHU_URL
    import importlib.util
    spec = importlib.util.spec_from_file_location("blm", str(DIR / "backfill_ly_metrics.py"))
    blm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(blm)

    data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    if not ensure_proxy():
        print("proxy fail"); return 1
    tid = cdp_new(FEISHU_URL)
    time.sleep(8)
    problems = []
    tried = []
    for mk, layout in sorted(MONTH_LAYOUT.items()):
        tab = layout["tab"]
        print(f"== {tab} ==")
        r = blm.activate_tab(tid, tab)
        time.sleep(10)
        # 当期
        cur, cerr = _read_block(tid, layout, "cur")
        # 同期（7月当期不走登记列，用 extract_july_data；ly 仍按登记）
        ly, lerr = _read_block(tid, layout, "ly")
        if cerr or lerr:
            print(f"  读表失败 cur={str(cerr)[:60]} ly={str(lerr)[:60]}")
            tried.append(mk); continue
        days = {d["d"] for d in data["months"][mk]["days"] if d.get("a")}
        # 当期比 a/post_refund（7月除外：当期用 extract_july_data 补）
        if mk != "7":
            for d in data["months"][mk]["days"]:
                key = f"{mk}-{d['d']}"
                row = cur.get(key)
                if not row or not d.get("a"):
                    continue
                for f in CUR_VALUE_COLS:
                    col = CUR_VALUE_COLS[f]
                    v = row.get("c" + str(col))
                    if isinstance(v, (int, float)) and isinstance(d.get(f), (int, float)) and abs(v - d[f]) > 1:
                        problems.append(f"{mk}月{d['d']} 当期{f}: 源表{v} vs data.json {d[f]}")
        # ly 比（按登记可用列）
        ly_pairs = [("ly_a", "a"), ("ly_post_refund", "post_refund"),
                    ("ly_v", "v"), ("ly_b", "b"), ("ly_conv", "conv"),
                    ("ly_refund_amt", "refund_amt"), ("ly_aov", "aov"),
                    ("ly_cart_users", "cart_users"), ("ly_ref", "ref"),
                    ("ly_cart_rate", "cart_rate"), ("ly_cart_conv", "cart_conv")]
        for d in data["months"][mk]["days"]:
            key = f"{mk}-{d['d']}"
            if not d.get("a") or d.get("ly_post_refund") is None:
                continue
            row = ly.get(key)
            if not row:
                continue
            for lyf, curf in ly_pairs:
                col = layout["ly"].get(lyf)
                if col is None:
                    continue
                v = row.get("c" + str(col))
                lv = d.get(lyf)
                if isinstance(v, (int, float)) and isinstance(lv, (int, float)):
                    if lyf in ("ly_ref", "ly_cart_rate", "ly_cart_conv"):
                        v = v * 100   # 源表存小数, data.json 存百分数
                        tol = 0.1
                    elif lyf == "ly_conv":
                        tol = 1e-4
                    else:
                        tol = 1.0
                    if abs(v - lv) > tol:
                        problems.append(f"{mk}月{d['d']} 同期{lyf}: 源表{v} vs data.json {lv} (cur {d.get(curf)})")
    cdp_close(tid)

    if problems:
        print(f"\n⚠ 对账发现 {len(problems)} 处不一致:")
        for p in problems[:25]:
            print("  " + p)
        notify("⚠ 看板与飞书源表不一致", "共" + str(len(problems)) + "处，详见 daily_pull.log")
        return 1
    print("\n✓ 对账通过：7/8/9月抽样关键列与 data.json 一致")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--coverage":
        sys.exit(coverage_check())
    elif len(sys.argv) > 1 and sys.argv[1] == "--reconcile":
        sys.exit(reconcile())
    else:
        print("usage: verify_data.py --coverage | --reconcile")
