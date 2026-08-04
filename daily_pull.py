#!/usr/bin/env python3
"""每日自动从飞书维表拉取数据，更新业绩看板

依赖:
  - 专用 headless Chrome（端口 9223，profile 在 ~/.local/share/dashboard/chrome-profile）
  - CDP Proxy (cdp-proxy.mjs 自动启动, --browser=dashboard)

运行: python3 daily_pull.py
计划任务 (launchd): 每天 10:00 自动执行
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# ==================== 配置 ====================
FEISHU_URL = "https://vcnrz1ae7b5x.feishu.cn/wiki/LkD9wO05BiZrSkkLYg7czErwn5e"
CDP_PROXY = "http://localhost:3456"
DATA_DIR = Path(__file__).parent
DATA_JSON = DATA_DIR / "data.json"

# 专用无头浏览器（独立端口 + 独立 profile，绕开日常 Chrome 的失效 CDP）
CHROME_PORT = 9223
CHROME_PROFILE = Path(os.path.expanduser("~/.local/share/dashboard/chrome-profile"))

SHEET_DOMAIN = "vcnrz1ae7b5x.feishu.cn"

# 7月表配置：第一段（rows 0-33）是 2026年 看板实际数据
# 第二段（rows 37-49）是 2025年 历史对比，用于计算前同比
SHEET7 = {
    # 2025段提取配置（用于计算前同比）
    "ly_section": {
        "gmv_actual": {"idx": 39, "label": "GMV达成"},  # 2025年GMV达成
    },
    # 2025段的行索引列表
    "ly_row_indices": ["39"],
    "data_col_start": 4,   # col 4 = 7月1日
    "data_col_end": 34,    # col 34 = 7月31日
    "rows": {
        "weekday":    {"idx": 0,  "label": "星期"},
        "date":       {"idx": 2,  "label": "日期"},
        "gmv_target": {"idx": 5,  "label": "GMV目标"},
        "gmv_actual": {"idx": 6,  "label": "业绩达成"},
        "refund_amt": {"idx": 10, "label": "退款金额达成"},
        "post_refund":{"idx": 12, "label": "去退金额达成"},
        "visitors":   {"idx": 21, "label": "访客达成"},
        "buyers":     {"idx": 23, "label": "买家达成"},
        "aov":        {"idx": 27, "label": "客单价达成"},
        "ref_rate":   {"idx": 16, "label": "退款率达成"},
        "yoy":        {"idx": 8,  "label": "前同比"},
        "cart_users": {"idx": 29, "label": "加购人数达成"},
        "cart_rate":  {"idx": 31, "label": "加购率达成"},
        "cart_conv":  {"idx": 33, "label": "加购转化率达成"},
        # 各指标目标（从飞书提取）
        "visitors_target":   {"idx": 20, "label": "访客目标"},
        "buyers_target":     {"idx": 22, "label": "买家目标"},
        "aov_target":        {"idx": 26, "label": "客单价目标"},
        "ref_rate_target":   {"idx": 15, "label": "退款率目标"},
        "refund_amt_target": {"idx": 9,  "label": "退款金额目标"},
        "post_refund_target":{"idx": 11, "label": "去退金额目标"},
        "conv_rate_target":  {"idx": 24, "label": "转化率目标"},
        "cart_users_target": {"idx": 28, "label": "加购人数目标"},
        "cart_rate_target":  {"idx": 30, "label": "加购率目标"},
        "cart_conv_target":  {"idx": 32, "label": "加购转化率目标"},
    },
}

WEEKDAY_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7}

# 8月表：新版引擎，用 getValue 行式读取。row1=表头, row2-32=8月1-31日（每行一天）
AUG_COLS = {
    "date": 3, "rhythm": 0, "weekday": 2, "t": 4, "a": 5,
    "y": 7,
    "refund_amt": 9, "refund_amt_t": 8,
    "post_refund": 11, "post_refund_t": 10,
    "ref": 15, "ref_t": 14,
    "v": 21, "v_t": 20, "b": 23, "b_t": 22,
    "conv": 25, "conv_t": 24, "aov": 27, "aov_t": 26,
    "cart_users": 29, "cart_users_t": 28, "cart_rate": 31, "cart_rate_t": 30,
    "cart_conv": 33, "cart_conv_t": 32,
    "y_net": 13,
}
AUG_FIRST_ROW = 2
AUG_LAST_ROW = 32
AUG_TARGET = 34000000  # 8月业绩目标（row0 汇总）

# ==================== CDP 操作 ====================

# 自动启动的 Chrome 进程，退出时清理
_auto_chrome_pid = None
_auto_proxy_pid = None


def find_chrome():
    """查找 Chrome 可执行文件路径"""
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        os.path.expanduser("~/.chrome/chrome-mac/Chromium.app/Contents/MacOS/Chromium"),
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    # which chrome
    try:
        r = subprocess.run(["which", "google-chrome", "chrome", "chromium"],
                           capture_output=True, text=True, timeout=5)
        for line in r.stdout.strip().splitlines():
            if line and Path(line).exists():
                return line
    except:  # noqa
        pass
    return None


def start_headless_chrome():
    """启动无头 Chrome，返回 (pid, port) 或 None"""
    chrome_path = find_chrome()
    if not chrome_path:
        print("  ⚠ Chrome 未安装，无法启动无头模式")
        return None

    port = CHROME_PORT
    # 检查端口是否已有可用的 Chrome（CDP 正常才复用）
    try:
        r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                            f"http://localhost:{port}/json/version"], capture_output=True,
                           text=True, timeout=3)
        if r.stdout.strip() == "200":
            return None  # 已有 Chrome 在运行
    except:  # noqa
        pass

    print(f"  启动无头 Chrome (port {port})...")
    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [chrome_path,
         "--headless=new",
         f"--remote-debugging-port={port}",
         f"--user-data-dir={CHROME_PROFILE}",
         "--no-first-run",
         "--no-default-browser-check",
         "--disable-gpu",
         "--disable-software-rasterizer",
         "--mute-audio",
         "--window-size=1280,800"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 等待 Chrome 就绪
    for _ in range(20):
        time.sleep(0.5)
        try:
            r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                                f"http://localhost:{port}/json/version"],
                               capture_output=True, text=True, timeout=3)
            if r.stdout.strip() == "200":
                print(f"  ✓ 无头 Chrome 已就绪 (PID {proc.pid})")
                return proc.pid
        except:  # noqa
            pass

    print("  ✗ 无头 Chrome 启动超时")
    try:
        proc.kill()
    except:  # noqa
        pass
    return None


def proxy_connected():
    """CDP Proxy 真正连上浏览器的标志：/targets 返回 JSON 数组。
    仅返回 HTTP 200 不够——proxy 连不上浏览器时也会 200 但 body 是 {"error":...}。"""
    try:
        r = subprocess.run(["curl", "-s", "-m", "5", f"{CDP_PROXY}/targets"],
                           capture_output=True, text=True, timeout=8)
        return isinstance(json.loads(r.stdout), list)
    except Exception:  # noqa
        return False


def ensure_proxy():
    """确保专用 headless Chrome 和 CDP Proxy 已启动"""
    global _auto_chrome_pid, _auto_proxy_pid

    # 1) 先启动（或复用）专用 headless Chrome，确保 CDP 端口就绪
    chrome_pid = start_headless_chrome()
    if chrome_pid:
        _auto_chrome_pid = chrome_pid

    # 2) Proxy 已连上浏览器 → 直接可用
    if proxy_connected():
        return True

    # 3) 有残留 proxy 但未连上浏览器（坏 proxy）→ 杀掉重启
    try:
        subprocess.run(["pkill", "-f", "cdp-proxy.mjs"], timeout=5)
        time.sleep(1)
    except Exception:  # noqa
        pass
    _auto_proxy_pid = None

    # 4) 启动新 CDP Proxy，明确连接专用 Chrome
    proxy_script = "/Users/luoxiaomin/.claude/skills/web-access/scripts/cdp-proxy.mjs"
    if not Path(proxy_script).exists():
        return False
    try:
        proc = subprocess.Popen(["node", proxy_script, "--browser=dashboard"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _auto_proxy_pid = proc.pid
        for _ in range(20):
            time.sleep(1)
            if proxy_connected():
                print("  ✓ CDP Proxy 已连接无头 Chrome")
                return True
    except Exception:  # noqa
        pass

    return False


def cleanup_headless():
    """清理自动启动的 Chrome 和 Proxy"""
    if _auto_chrome_pid:
        try:
            subprocess.run(["kill", str(_auto_chrome_pid)], timeout=5)
            print(f"  ✓ 无头 Chrome 已停止 (PID {_auto_chrome_pid})")
        except:  # noqa
            pass
    if _auto_proxy_pid:
        try:
            subprocess.run(["kill", str(_auto_proxy_pid)], timeout=5)
        except:  # noqa
            pass

def cdp_new(url, timeout=20):
    r = subprocess.run(["curl", "-s", "-m", str(timeout), "-X", "POST", "--data-raw", url,
                        f"{CDP_PROXY}/new"], capture_output=True, text=True, timeout=timeout + 5)
    try:
        return json.loads(r.stdout).get("targetId", "")
    except (json.JSONDecodeError, KeyError, TypeError):
        return ""

def cdp_eval(target_id, js, timeout=20):
    r = subprocess.run(["curl", "-s", "-m", str(timeout), "-X", "POST",
                        f"{CDP_PROXY}/eval?target={target_id}",
                        "-d", js], capture_output=True, text=True, timeout=timeout + 5)
    try:
        return json.loads(r.stdout).get("value")
    except (json.JSONDecodeError, KeyError, TypeError):
        return None

def cdp_close(target_id):
    try:
        subprocess.run(["curl", "-s", f"{CDP_PROXY}/close?target={target_id}"],
                       capture_output=True, timeout=5)
    except:  # noqa
        pass

# ==================== 数据转换 ====================

def excel_serial_to_date(serial):
    """Excel 序列号 → date; 处理 1900-02-29 闰年 bug"""
    s = int(serial)
    if s > 60:
        s -= 1
    return datetime(1899, 12, 31) + timedelta(days=s)

def normalize_table(raw):
    """将 variantModel.table 统一为 dict[int, dict]"""
    if isinstance(raw, list):
        return {str(i): item for i, item in enumerate(raw) if item}
    return raw

def get_cell(table, row_idx_str, col_str):
    """从 variantModel.table 读取单元格 value"""
    row = table.get(row_idx_str) if isinstance(table, dict) else (
        table[int(row_idx_str)] if int(row_idx_str) < len(table) else None
    )
    if not row or not row.get("data"):
        return None
    data = row["data"]
    if isinstance(data, dict):
        cell = data.get(col_str)
    elif isinstance(data, list):
        col = int(col_str)
        cell = data[col] if col < len(data) else None
    else:
        return None
    return cell.get("value") if cell else None

def extract_july_data(target_id):
    """激活7月tab并提取第2区块的每日数据"""
    # 激活 7月 tab
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
                    h.onMouseDown({
                        type:'mousedown', button:0, buttons:1,
                        clientX:0, clientY:0,
                        target:target, currentTarget:target,
                        preventDefault:function(){},
                        stopPropagation:function(){}
                    });
                    return 'ACTIVATED';
                }
            }
        }
        return 'NO_HANDLER';
    })()
    """
    result = cdp_eval(target_id, js_activate)
    if result != "ACTIVATED":
        return None, f"激活7月tab失败: {result}"

    # 等待异步数据加载
    print("   等待数据加载...")
    time.sleep(15)

    # 提取各行的列数据（防循环引用）
    row_indices = set()
    for cfg in SHEET7["rows"].values():
        row_indices.add(str(cfg["idx"]))
    # 同时提取2025段数据（用于计算前同比等）
    for idx in SHEET7.get("ly_row_indices", []):
        row_indices.add(idx)
    rows_json = ",".join(f'"{i}":[]' for i in sorted(row_indices, key=int))
    js_extract = f"""
    (function(){{
        try {{
            var s = window.spread.getActiveSheet();
            var t = s._dataModel.contentModel.variantModel.table;
            var result = {{}};
            {''.join(f'result["{i}"] = []; try {{ var r = t["{i}"]; if(r && r.data) {{ for(var c=1; c<70; c++) {{ var cell = r.data[String(c)]; if(cell && cell.value !== undefined && cell.value !== null) result["{i}"].push(cell.value); else result["{i}"].push(null); }} }} }} catch(e){{}}' for i in sorted(row_indices, key=int))}
            return JSON.stringify(result);
        }} catch(e) {{ return 'ERROR: ' + e.message; }}
    }})()
    """
    raw = cdp_eval(target_id, js_extract)
    if not raw or raw.startswith("ERROR"):
        return None, f"提取数据表失败: {str(raw)[:100]}"

    try:
        raw_data = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"JSON解析失败: {e}"

    # Build a simple table: row_idx -> [col1_val, col2_val, ...]
    table = {}
    for row_idx_str, values in raw_data.items():
        table[row_idx_str] = values

    def get_val(row_idx_str, col_idx):
        arr = table.get(row_idx_str)
        if not arr or col_idx - 1 >= len(arr):
            return None
        return arr[col_idx - 1]

    col_start = SHEET7["data_col_start"]
    col_end = SHEET7["data_col_end"]
    days = []
    for col in range(col_start, col_end + 1):
        # 日期
        date_val = get_val(str(SHEET7["rows"]["date"]["idx"]), col)
        if not isinstance(date_val, (int, float)):
            continue
        dt = excel_serial_to_date(date_val)
        day_str = dt.strftime("%d")

        # 星期
        w_val = WEEKDAY_MAP.get(str(get_val(str(SHEET7["rows"]["weekday"]["idx"]), col) or ""), 0)

        # GMV目标
        t_val = int(get_val(str(SHEET7["rows"]["gmv_target"]["idx"]), col) or 0)

        # 构建日数据
        day = {"d": day_str, "r": "—", "w": w_val, "t": t_val}

        for field, cfg in SHEET7["rows"].items():
            if field in ("date", "weekday", "gmv_target"):
                continue

            val = get_val(str(cfg["idx"]), col)

            if field == "gmv_actual":
                day["a"] = int(val) if isinstance(val, (int, float)) else 0
            elif field == "ref_rate":
                day["ref"] = round(val * 100, 1) if isinstance(val, (int, float)) else 0
            elif field == "aov":
                day["aov"] = int(val) if isinstance(val, (int, float)) else 0
            elif field in ("refund_amt", "post_refund"):
                day[field] = int(val) if isinstance(val, (int, float)) else 0
            elif field == "cart_users":
                day[field] = int(val) if isinstance(val, (int, float)) else 0
            elif field == "cart_rate":
                day[field] = round(val * 100, 1) if isinstance(val, (int, float)) else 0
            elif field == "cart_conv":
                day[field] = round(val * 100, 1) if isinstance(val, (int, float)) else 0
            elif field == "visitors":
                day["v"] = int(val) if isinstance(val, (int, float)) else 0
            elif field == "buyers":
                day["b"] = int(val) if isinstance(val, (int, float)) else 0
            elif field == "yoy":
                if isinstance(val, (int, float)) and val > -1:
                    day["y"] = round(val * 100, 1)
                else:
                    day["y"] = None
            # 目标字段
            elif field == "visitors_target":
                day["v_t"] = int(val) if isinstance(val, (int, float)) else 0
            elif field == "buyers_target":
                day["b_t"] = int(val) if isinstance(val, (int, float)) else 0
            elif field == "aov_target":
                day["aov_t"] = int(val) if isinstance(val, (int, float)) else 0
            elif field == "ref_rate_target":
                day["ref_t"] = round(val * 100, 1) if isinstance(val, (int, float)) else 0
            elif field in ("refund_amt_target", "post_refund_target"):
                day[field.replace("_target", "_t")] = int(val) if isinstance(val, (int, float)) else 0
            elif field == "conv_rate_target":
                day["conv_t"] = val if isinstance(val, (int, float)) else 0
            elif field == "cart_users_target":
                day["cart_users_t"] = int(val) if isinstance(val, (int, float)) else 0
            elif field == "cart_rate_target":
                day["cart_rate_t"] = round(val * 100, 1) if isinstance(val, (int, float)) else 0
            elif field == "cart_conv_target":
                day["cart_conv_t"] = round(val * 100, 1) if isinstance(val, (int, float)) else 0

        # 达成率 = a / t
        day["rr"] = round(day["a"] / day["t"], 3) if day["t"] > 0 else 0

        # 自动计算派生字段（飞书某些行可能为空）
        # 去退金额 = 实际业绩 - 退款金额（如飞书行无数据则计算）
        if day.get("post_refund", 0) == 0 and day["a"] > 0 and day.get("refund_amt", 0) > 0:
            day["post_refund"] = day["a"] - day["refund_amt"]
        # 加购率 = 加购人数 / 访客 * 100
        if day.get("cart_rate", 0) == 0 and day.get("cart_users", 0) > 0 and day.get("v", 0) > 0:
            day["cart_rate"] = round(day["cart_users"] / day["v"] * 100, 1)
        # 加购转化率 = 买家 / 加购人数 * 100
        if day.get("cart_conv", 0) == 0 and day.get("b", 0) > 0 and day.get("cart_users", 0) > 0:
            day["cart_conv"] = round(day["b"] / day["cart_users"] * 100, 1)

        # 前同比：7月tab"前同比"行本身无数据，用2025段GMV达成计算
        # (2026业绩 - 2025业绩) / 2025业绩 * 100
        if day.get("y") is None and day["a"] > 0:
            ly_cfg = SHEET7.get("ly_section", {}).get("gmv_actual")
            if ly_cfg:
                ly_val = get_val(str(ly_cfg["idx"]), col)
                if isinstance(ly_val, (int, float)) and ly_val > 0:
                    day["y"] = round((day["a"] - ly_val) / ly_val * 100, 1)

        days.append(day)

    return days, None


def extract_august_data(target_id):
    """新版引擎（getValue 行式）读取8月逐日数据"""
    js_activate = """
    (function(){
        var tabs = document.querySelectorAll('.tab-list > div');
        var target = null;
        tabs.forEach(function(t){
            if (t.textContent.trim() === '8月') target = t;
        });
        if (!target) return 'NO_TAB';
        var keys = Object.keys(target);
        for (var i = 0; i < keys.length; i++) {
            if (keys[i].startsWith('__reactEventHandlers')) {
                var h = target[keys[i]];
                if (h && h.onMouseDown) {
                    h.onMouseDown({
                        type:'mousedown', button:0, buttons:1,
                        clientX:0, clientY:0,
                        target:target, currentTarget:target,
                        preventDefault:function(){},
                        stopPropagation:function(){}
                    });
                    return 'ACTIVATED';
                }
            }
        }
        return 'NO_HANDLER';
    })()
    """
    result = cdp_eval(target_id, js_activate)
    if result != "ACTIVATED":
        return None, f"激活8月tab失败: {result}"

    print("   等待数据加载...")
    time.sleep(15)

    cols = ",".join(str(c) for c in sorted(set(AUG_COLS.values())))
    js_read = (
        "(function(){var s=window.spread.getActiveSheet();var out=[];"
        "for(var r=" + str(AUG_FIRST_ROW) + ";r<=" + str(AUG_LAST_ROW) + ";r++){"
        "var row={};var cols=[" + cols + "];"
        "cols.forEach(function(c){var v;try{v=s.getValue(r,c);}catch(e){v='E';}"
        "row['c'+c]=v===undefined||v===null?null:v;});out.push(row);}"
        "return JSON.stringify(out);})()"
    )
    raw = cdp_eval(target_id, js_read)
    if not raw or not raw.startswith("["):
        return None, f"读取8月数据失败: {str(raw)[:100]}"
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"8月JSON解析失败: {e}"

    def gv(row, col):
        v = row.get("c" + str(col))
        return v if isinstance(v, (int, float)) else None

    days = []
    for row in rows:
        d_date = gv(row, AUG_COLS["date"])
        if d_date is None:
            continue
        dt = excel_serial_to_date(d_date)
        day = {
            "d": dt.strftime("%d"),
            "r": gv(row, AUG_COLS["rhythm"]) or "—",
            "w": WEEKDAY_MAP.get(str(gv(row, AUG_COLS["weekday"]) or ""), 0),
            "t": int(gv(row, AUG_COLS["t"]) or 0),
        }

        day["a"] = int(gv(row, AUG_COLS["a"]) or 0)
        day["rr"] = round(day["a"] / day["t"], 3) if day["t"] > 0 else 0

        y = gv(row, AUG_COLS["y"])
        day["y"] = round(y * 100, 1) if isinstance(y, (int, float)) and y > -0.999 else None

        day["refund_amt"] = int(gv(row, AUG_COLS["refund_amt"]) or 0)
        day["refund_amt_t"] = int(gv(row, AUG_COLS["refund_amt_t"]) or 0)
        day["post_refund"] = int(gv(row, AUG_COLS["post_refund"]) or 0)
        day["post_refund_t"] = int(gv(row, AUG_COLS["post_refund_t"]) or 0)

        ref = gv(row, AUG_COLS["ref"])
        day["ref"] = round(ref * 100, 1) if isinstance(ref, (int, float)) else 0
        ref_t = gv(row, AUG_COLS["ref_t"])
        day["ref_t"] = round(ref_t * 100, 1) if isinstance(ref_t, (int, float)) else 0

        day["v"] = int(gv(row, AUG_COLS["v"]) or 0)
        day["v_t"] = int(gv(row, AUG_COLS["v_t"]) or 0)
        day["b"] = int(gv(row, AUG_COLS["b"]) or 0)
        day["b_t"] = int(gv(row, AUG_COLS["b_t"]) or 0)
        day["conv"] = gv(row, AUG_COLS["conv"])
        day["conv_t"] = gv(row, AUG_COLS["conv_t"])
        day["aov"] = int(gv(row, AUG_COLS["aov"]) or 0)
        day["aov_t"] = int(gv(row, AUG_COLS["aov_t"]) or 0)
        day["cart_users"] = int(gv(row, AUG_COLS["cart_users"]) or 0)
        day["cart_users_t"] = int(gv(row, AUG_COLS["cart_users_t"]) or 0)
        cart_rate = gv(row, AUG_COLS["cart_rate"])
        day["cart_rate"] = round(cart_rate * 100, 1) if isinstance(cart_rate, (int, float)) else 0
        cart_rate_t = gv(row, AUG_COLS["cart_rate_t"])
        day["cart_rate_t"] = round(cart_rate_t * 100, 1) if isinstance(cart_rate_t, (int, float)) else 0
        cart_conv = gv(row, AUG_COLS["cart_conv"])
        day["cart_conv"] = round(cart_conv * 100, 1) if isinstance(cart_conv, (int, float)) else 0
        cart_conv_t = gv(row, AUG_COLS["cart_conv_t"])
        day["cart_conv_t"] = round(cart_conv_t * 100, 1) if isinstance(cart_conv_t, (int, float)) else 0

        y_net = gv(row, AUG_COLS["y_net"])
        day["y_net"] = round(y_net * 100, 1) if isinstance(y_net, (int, float)) and y_net > -0.999 else None

        days.append(day)

    return days, None


def merge_days(existing_days, new_days):
    """合并新数据到已有数据，只覆盖非零实际值"""
    existing_map = {d["d"]: d for d in existing_days}

    for nd in new_days:
        d_key = nd["d"]
        if d_key in existing_map:
            ed = existing_map[d_key]
            # 保留原有字段（仅当新数据为空时）
            if nd.get("y") is None:
                nd["y"] = ed.get("y")
            nd["r"] = ed.get("r", "—")

            # 新数据无实际值（未来日期）→ 保留目标和节奏，清除实际值（避免继承旧年份数据）
            if nd["a"] == 0:
                nd["a"] = 0
                nd["rr"] = 0
                for f in ("ref", "v", "b", "aov", "refund_amt", "post_refund",
                           "cart_users", "cart_rate", "cart_conv"):
                    nd[f] = 0
                nd["rr"] = round(nd["a"] / nd["t"], 3) if nd["t"] > 0 else 0

            # 飞书某些行可能为空（如7月去退金额达成行无数据），保留已有非零值
            if nd["a"] > 0:
                for f in ("refund_amt", "post_refund", "v", "b", "aov", "ref",
                           "cart_users", "cart_rate", "cart_conv"):
                    if nd.get(f) == 0 and ed.get(f, 0) > 0:
                        nd[f] = ed[f]

            # 目标值始终保留（飞书目标行稳定，新数据可能为空）
            for f in ("v_t", "b_t", "aov_t", "ref_t", "refund_amt_t", "post_refund_t",
                       "conv_t", "cart_users_t", "cart_rate_t", "cart_conv_t"):
                if nd.get(f) in (0, None, 0.0) and ed.get(f, 0) > 0:
                    nd[f] = ed[f]

            # 保留来自 backfill_yoy_net.py 的外部注入字段
            for f in ("ly_post_refund", "y_net"):
                if ed.get(f) is not None and nd.get(f) is None:
                    nd[f] = ed[f]

            # 目标优先用新值（真实目标），回退已有
            nd["t"] = nd["t"] if nd["t"] > 0 else ed.get("t", nd["t"])

            # weekday始终更新
            if nd["w"] == 0:
                nd["w"] = ed.get("w", 0)

            existing_map[d_key] = nd
        elif nd["a"] > 0:
            existing_map[d_key] = nd

    result = sorted(existing_map.values(), key=lambda d: d["d"])
    return result


def main():
    t0 = time.time()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始每日数据更新...")

    # 加载现有数据
    if not DATA_JSON.exists():
        print(f"  ✗ {DATA_JSON} 不存在")
        sys.exit(1)

    data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    old_count = sum(len(m["days"]) for m in data["months"].values())
    old_actual = sum(m["actual"] for m in data["months"].values())
    print(f"  当前: {len(data['months'])}月, {old_count}天, 年达成 {old_actual/10000:.0f}万")

    # 确保 CDP Proxy
    print(f"  检查 CDP Proxy...")
    if not ensure_proxy():
        print("  ✗ CDP Proxy 不可用")
        cleanup_headless()
        sys.exit(1)

    # 打开飞书 + 提取，失败自动重试（页面加载慢/偶发NO_TAB）
    MAX_RETRY = 3
    july_days, aug_days, err = None, None, "未尝试"
    tid = ""
    for attempt in range(1, MAX_RETRY + 1):
        print(f"  打开飞书页面 (第{attempt}次尝试)...")
        tid = cdp_new(FEISHU_URL)
        if not tid:
            print("  ✗ 飞书页面打开失败")
            time.sleep(10)
            continue
        time.sleep(8)
        print(f"  提取7月数据...")
        july_days, july_err = extract_july_data(tid)
        print(f"  提取8月数据...")
        aug_days, aug_err = extract_august_data(tid)
        cdp_close(tid)
        if not july_err and not aug_err:
            err = None
            break
        err = july_err or aug_err
        print(f"  ✗ 第{attempt}次失败: {err}")
        if attempt < MAX_RETRY:
            time.sleep(10)

    try:
        if err:
            print(f"  ✗ 最终失败: {err}")
            # 不退出 — 仍运行 fix_data.py 更新 timestamp
        else:
            saved = False

            if july_days:
                print(f"  ✓ 7月提取 {len(july_days)} 天 (含 {sum(1 for d in july_days if d['a']>0)} 天有实际值)")
                month7 = data["months"]["7"]
                month7["days"] = merge_days(month7["days"], july_days)
                month7["actual"] = sum(d["a"] for d in month7["days"])
                month7["rate"] = round(month7["actual"] / month7["target"], 3) if month7["target"] > 0 else 0
                saved = True
            else:
                print("  7月无新数据")

            if aug_days:
                # 8月首次出现时初始化结构
                if "8" not in data["months"]:
                    data["months"]["8"] = {
                        "name": "8月", "label": "8月",
                        "target": AUG_TARGET, "actual": 0, "rate": 0,
                        "days": [{"d": "%02d" % d, "r": "—", "w": 0,
                                  "t": 0, "a": 0, "rr": 0} for d in range(1, 32)],
                    }
                    data["yearTarget"] = data.get("yearTarget", 0) + AUG_TARGET

                print(f"  ✓ 8月提取 {len(aug_days)} 天 (含 {sum(1 for d in aug_days if d['a']>0)} 天有实际值)")
                month8 = data["months"]["8"]
                month8["days"] = merge_days(month8["days"], aug_days)
                month8["actual"] = sum(d["a"] for d in month8["days"])
                month8["rate"] = round(month8["actual"] / month8["target"], 3) if month8["target"] > 0 else 0
                saved = True
            else:
                print("  8月无新数据")

            if saved:
                data["yearActual"] = sum(m["actual"] for m in data["months"].values())
                data["lastUpdated"] = datetime.now().strftime("%Y-%m-%d")
                DATA_JSON.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                new_count = sum(len(m["days"]) for m in data["months"].values())
                new_actual = sum(m["actual"] for m in data["months"].values())
                print(f"  ✓ 保存: {len(data['months'])}月, {new_count}天, 年达成 {new_actual/10000:.0f}万")
                if new_actual != old_actual:
                    print(f"  Δ 年达成变化: {old_actual/10000:.0f}万 → {new_actual/10000:.0f}万")

        # 运行 fix_data.py 生成 data.js
        print(f"  生成 data.js...")
        subprocess.run([sys.executable, str(DATA_DIR / "fix_data.py")],
                       cwd=str(DATA_DIR), capture_output=True)
    finally:
        cleanup_headless()
        cdp_close(tid)

    elapsed = time.time() - t0
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 完成 ({elapsed:.0f}s)")


if __name__ == "__main__":
    main()
