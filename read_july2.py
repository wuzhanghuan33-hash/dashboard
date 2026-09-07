#!/usr/bin/env python3
"""复用 daily_pull.extract_july_data 读取7月完整数据，导出 JSON"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from daily_pull import cdp_new, cdp_close, extract_july_data, SHEET7  # noqa: E402

HERE = Path(__file__).parent
target = cdp_new("https://vcnrz1ae7b5x.feishu.cn/wiki/LkD9wO05BiZrSkkLYg7czErwn5e")
print("target:", target)

result = extract_july_data(target)
if result[0] is None:
    print("FAILED:", result[1])
    cdp_close(target)
    sys.exit(1)

days = result[0]
print(f"读取成功: {len(days)} 天")

# days 是列表，每项是 {date, gmv_target, gmv_actual, ...} 已由 extract_july_data 整理
out = HERE / "july_data.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(days, f, ensure_ascii=False, indent=1)

# 摘要
if days:
    print("首日样例:", json.dumps(days[0], ensure_ascii=False)[:400])
print("saved to", out)
cdp_close(target)
