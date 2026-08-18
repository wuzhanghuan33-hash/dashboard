#!/usr/bin/env python3
"""生成 category_data.js 供看板类目区块使用

读 qqdocs_category.json（腾讯文档类目拆解提取结果）→ 转成看板友好格式:
  const CATEGORY_DATA = { 类目: [ {d:"01-01", r:节奏, t:目标, a:达成, rr:达成率, refund:退款达成, post:去退达成}, ... ] }
"""
import json
from pathlib import Path

CAT_FILE = Path(__file__).with_name("qqdocs_category.json")
OUT_FILE = Path(__file__).with_name("category_data.js")


def main():
    cat = json.loads(CAT_FILE.read_text(encoding="utf-8"))
    cats = cat["categories"]

    out = {}
    for name, data in cats.items():
        days = []
        for d in data["days"]:
            t = d.get("t")
            a = d.get("a")
            rr = round(a / t, 4) if isinstance(t, (int, float)) and t > 0 and isinstance(a, (int, float)) else None
            ly_a = d.get("ly_a")
            ly_post = d.get("ly_post")
            # 厨电同比列单位不统一: 1-7月为万元, 8月起为元。值<10000 视为万元×10000, 否则已是元
            if name == "厨电":
                if isinstance(ly_a, (int, float)) and ly_a < 10000:
                    ly_a = ly_a * 10000
                if isinstance(ly_post, (int, float)) and ly_post < 10000:
                    ly_post = ly_post * 10000
            # 同期对齐归一：当期无达成(a 空/0)的天，清除同期字段，防止未来天虚增同比分母
            has_actual = isinstance(a, (int, float)) and a > 0
            days.append({
                "d": d["date"][5:],          # "01-01"
                "r": d.get("rhythm") or "",
                "t": t if isinstance(t, (int, float)) else None,
                "a": a if isinstance(a, (int, float)) else None,
                "rr": rr,
                "refund": d.get("refund_a") if isinstance(d.get("refund_a"), (int, float)) else None,
                "post": d.get("post_a") if isinstance(d.get("post_a"), (int, float)) else None,
                "ly_a": (ly_a if isinstance(ly_a, (int, float)) else None) if has_actual else None,
                "ly_post": (ly_post if isinstance(ly_post, (int, float)) else None) if has_actual else None,
            })
        out[name] = days

    OUT_FILE.write_text(
        "// Auto-generated from qqdocs_category.json - 重新提取后运行 gen_category_js.py\n"
        "const CATEGORY_DATA = " + json.dumps(out, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )

    for name, days in out.items():
        print(f"{name}: {len(days)}天 首={days[0]['d']} 末={days[-1]['d']}")
    print(f"✓ 已写入 {OUT_FILE}")


if __name__ == "__main__":
    main()
