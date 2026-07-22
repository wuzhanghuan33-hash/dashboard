#!/usr/bin/env python3
"""数据每日更新工具

用法：
  1. 编辑 data.json 添加新的一天数据（格式见下方）
  2. 运行 python3 fix_data.py
  3. 刷新浏览器 http://localhost:8080

新增数据格式（复制到对应的月份 days 数组中）：
  {"d": "01", "r": "活动名称", "w": 1, "t": 1000000, "a": 850000,
   "rr": 0.85, "y": -5.2, "ref": 30.0, "v": 80000, "b": 600, "aov": 1417}

字段说明：
  d=日期, r=节奏, w=星期(1-7), t=目标, a=达成,
  rr=达成率小数, y=前同比(%), ref=退款率(%), v=访客, b=买家, aov=客单价
"""
import json
import re
import sys
import os

def fix_and_generate(data_path='data.json'):
    """修复data.json并生成data.js"""
    with open(data_path, 'r') as f:
        raw = f.read()

    # Fix .NN -> 0.NN
    raw = re.sub(r':\s*\.(\d+)', r': 0.\1', raw)

    # Fix bare keys
    bare_keys = ['d', 'r', 'w', 't', 'a', 'rr', 'y', 'ref', 'v', 'b', 'aov',
                 'refund_amt', 'post_refund', 'cart_users', 'cart_rate', 'cart_conv']
    pattern = r'\b(' + '|'.join(bare_keys) + r')(?=\s*:)'
    raw = re.sub(pattern, r'"\1"', raw)

    # Parse
    data = json.loads(raw)

    # Recalculate monthly totals
    for m_key, month in data['months'].items():
        days = month['days']
        total_a = sum(d['a'] for d in days)
        total_t = month['target']
        month['actual'] = total_a
        month['rate'] = round(total_a / total_t, 3) if total_t > 0 else 0

    # Recalculate year total
    year_actual = sum(m['actual'] for m in data['months'].values())
    data['yearActual'] = year_actual

    # Write proper JSON
    with open(data_path, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Write data.js
    js_dir = os.path.dirname(os.path.abspath(data_path))
    js_path = os.path.join(js_dir, 'data.js')
    with open(js_path, 'w') as f:
        f.write('// Auto-generated from data.json - 编辑data.json后运行此脚本\n')
        f.write(f'const DASHBOARD_DATA = {json.dumps(data, ensure_ascii=False)};\n')
        f.write('// 每日更新：编辑 data.json -> python3 fix_data.py -> 刷新浏览器\n')

    days_count = sum(len(m['days']) for m in data['months'].values())
    print(f"✓ 更新完成！{len(data['months'])}个月, {days_count}天数据")
    print(f"  H1目标: {data['yearTarget']/10000:.0f}万, H1达成: {year_actual/10000:.0f}万")
    print(f"  data.json ✓   data.js ✓")
    print(f"  → 刷新浏览器 http://localhost:8080")

if __name__ == '__main__':
    fix_and_generate()
