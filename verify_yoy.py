#!/usr/bin/env python3
"""防回归校验：同期(ly_*)必须与当期实际值对齐，错位则中止部署。

不变量（数据层归一 fix_data.py / gen_category_js.py 保证）：
  当期无实际业绩(a 空/0)的天 → 所有 ly_* 字段必须为 null
  一旦未来天残留同期 → 同比分母虚增（8月中曾出 -59.3% 假同比）

硬校验（违反即退出码 1，daily_pull.sh 据此中止部署）：
  - 未来天的任何 ly_* 残留
软告警（不失败，仅提示）：
  - 某月有实际数据但同期全空 → backfill 可能未注入，同比显示 — 而非错值

校验对象是「已归一的数据产物」：data.json（主看板）+ category_data.js（类别）。
不校验提取源 qqdocs_category.json / data 原稿——那些是未归一原始数据，未来天 ly_* 天然有值。

用法:
  python3 verify_yoy.py               # 校验 data.json + category_data.js
  python3 verify_yoy.py --no-cat      # 只校验 data.json（daily_pull.sh 用）
"""
import json
import sys
from pathlib import Path

DIR = Path(__file__).parent


def is_ly_key(k):
    return k.startswith("ly_") or k == "y_net"


def day_label(d):
    return d.get("date") or d.get("d") or "?"


def check_days(days, label):
    """返回 (hard_errs, warns)"""
    hard = []
    for d in days:
        a = d.get("a")
        has_actual = isinstance(a, (int, float)) and a > 0
        present = [k for k in d.keys() if is_ly_key(k) and d.get(k) is not None]
        if not has_actual and present:
            for k in present:
                hard.append(f"{label} {day_label(d)}: 当期无数据但 {k}={d.get(k)} 残留 → 同比会虚增")
    # 软告警：当月有实际数据的天，若整月 ly_* 全空 → backfill 未注入
    actual_days = [d for d in days if isinstance(d.get("a"), (int, float)) and d.get("a") > 0]
    with_ly = [d for d in actual_days if any(is_ly_key(k) and d.get(k) is not None for k in d.keys())]
    warns = []
    if actual_days and not with_ly:
        warns.append(f"{label}: {len(actual_days)}天有实际数据但同期全空 → backfill 可能未注入，同比显示 —")
    return hard, warns


def load_category_data():
    """从 category_data.js 读 CATEGORY_DATA 对象（已归一产物）"""
    s = (DIR / "category_data.js").read_text(encoding="utf-8")
    js = s[s.index("=") + 1:].strip()
    return json.loads(js[:js.rfind("};") + 1])


def main():
    check_cat = "--no-cat" not in sys.argv
    hard, warns = [], []
    data = json.loads((DIR / "data.json").read_text(encoding="utf-8"))
    for m, month in data["months"].items():
        h, w = check_days(month["days"], f"{m}月")
        hard += h; warns += w
    if check_cat and (DIR / "category_data.js").exists():
        try:
            cat = load_category_data()
            for name, days in cat.items():
                h, w = check_days(days, name)
                hard += h; warns += w
        except Exception as e:
            print(f"  ⚠ 读取 category_data.js 失败: {e}")

    for w in warns:
        print("  ⚠ " + w)
    if hard:
        print("✗ 同期对齐校验失败（存在错位）:")
        for e in hard[:30]:
            print("  " + e)
        print(f"共 {len(hard)} 处。数据不会上线，请检查 fix_data.py / gen_category_js.py 归一逻辑。")
        return 1
    print("✓ 同期对齐校验通过：所有未来天 ly_* 已清空")
    return 0


if __name__ == "__main__":
    sys.exit(main())
