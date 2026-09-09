#!/bin/bash
# 看板健康巡检 — 确定性聚合现有 check, 供 launchd 定时(每天16:35) + skill「看板巡检」手动调用
# 体检只读优先; 唯一自愈动作=拉 server.py(轻幂等); 数据/发布缺口只报不改(需人工拍板); 异常才 macOS 通知
# 输出写 health_check.log。状态三档用 ASCII: [ok] 正常 | [!!] 提示(仅日志) | [XX] 异常(日志+通知)
# 注意: 本脚本多字节字符只能出现在纯字面 echo, 严禁 `$var接多字节符号`(locale 依赖会截断, 曾致 ✓ 丢字节)
DIR="/Users/luoxiaomin/.local/share/dashboard"
LOG="$DIR/health_check.log"

notify() { osascript -e "display notification \"$1\" with title \"看板巡检\"" >/dev/null 2>&1; }

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 看板巡检 =====" >> "$LOG"
ANOM=0; WARN=0

# ---------- L1 调度与进程 ----------
L1=""
for j in daily-pull sycm-sync sycm-keepalive; do
  if launchctl list 2>/dev/null | grep -q "com.midea.dashboard.$j"; then
    L1="$L1 $j[ok]"
  else
    L1="$L1 $j[XX]缺"; ANOM=$((ANOM+1))
  fi
done
# 当前有任务在跑(属正常窗口外的残留才提示; 看门狗本应 40min 内兜底)
RUNNING=""
for pat in "daily_pull\.py" "sycm_sync\.py"; do
  pid=$(pgrep -f "$pat" | head -1)
  if [ -n "$pid" ]; then RUNNING="$RUNNING $pat"; fi
done
[ -n "$RUNNING" ] && L1="$L1 | 运行中:$RUNNING" && WARN=$((WARN+1))
# 今日看门狗触发次数(被兜底过 = 提示项)
WD=$(grep -c "\[看门狗\]" "$DIR/daily_pull.log" "$DIR/sycm_sync.log" 2>/dev/null | awk -F: '{s+=$2} END{print s+0}')
L1="$L1 | 今日看门狗触发:${WD}次"
[ "$WD" -gt 0 ] && WARN=$((WARN+1))
echo "[调度]$L1" >> "$LOG"

# ---------- L2 服务(唯一自愈 = server 8080, 幂等) ----------
SVC=""
svc() { # svc <name> <url> <match>  — url 含 http:// 不能冒号分隔, 用显式传参
  if curl -s --max-time 4 "$2" 2>/dev/null | grep -q "$3"; then
    SVC="$SVC $1[ok]"
  else
    SVC="$SVC $1[XX]"; ANOM=$((ANOM+1))
  fi
}
svc 9223 "http://localhost:9223/json/version" "Browser"
svc 9225 "http://localhost:9225/json/version" "Browser"
svc 3456 "http://localhost:3456/targets" "^\["
echo "[服务]$SVC" >> "$LOG"
# server 8080 单独处理: 自愈拉起
if curl -s -o /dev/null -w "%{http_code}" --max-time 3 http://localhost:8080/ 2>/dev/null | grep -q 200; then
  echo "[看板] server 8080[ok]" >> "$LOG"
else
  echo "[看板] server 8080[XX] 尝试自愈..." >> "$LOG"
  bash "$DIR/start_server.sh" >> "$LOG" 2>&1
  if curl -s -o /dev/null -w "%{http_code}" --max-time 3 http://localhost:8080/ 2>/dev/null | grep -q 200; then
    echo "[看板] server 8080 已由巡检拉起[ok]" >> "$LOG"
  else
    echo "[看板] server 8080 自愈失败[XX]" >> "$LOG"; ANOM=$((ANOM+1))
  fi
fi

# ---------- L3 数据(管道今日是否跑过 + 实际数据到哪天) ----------
DATA_INFO=$(/usr/bin/python3 - "$DIR" <<'PY'
import json, sys, datetime
DIR = sys.argv[1]
d = json.load(open(f"{DIR}/data.json"))
today = datetime.date.today()
lu = d.get("lastUpdated", "")
best = None
for mk, m in d.get("months", {}).items():
    try: mk = int(mk)
    except: continue
    if not isinstance(m, dict): continue
    for day in m.get("days", []):
        a = day.get("a")
        if a in (None, 0, "", 0.0): continue
        try: dd = int(day.get("d"))
        except: continue
        try: cur = datetime.date(today.year, mk, dd)
        except ValueError: continue
        if best is None or cur > best: best = cur
lag = (today - best).days if best else -1
last = best.strftime("%m-%d") if best else "无"
# lastUpdated 是否今天/昨天(判断今日管道是否生成过)
lu_ok = "no"
if lu:
    try:
        lu_d = datetime.datetime.strptime(str(lu)[:10], "%Y-%m-%d").date()
        lu_ok = "yes" if (today - lu_d).days <= 1 else "no"
    except Exception:
        lu_ok = "no"
print(f"{last}|{lag}|{lu_ok}|{str(lu)[:10]}")
PY
)
LAST=$(echo "$DATA_INFO" | cut -d'|' -f1); LAG=$(echo "$DATA_INFO" | cut -d'|' -f2)
LU_OK=$(echo "$DATA_INFO" | cut -d'|' -f3); LU_VAL=$(echo "$DATA_INFO" | cut -d'|' -f4)
if [ "$LU_OK" = "no" ]; then
  echo "[数据] 管道今日未生成 data.json(lastUpdated=$LU_VAL)[XX]"; ANOM=$((ANOM+1))
else
  if [ "$LAG" -le 1 ]; then ST="ok(正常节律)"
  elif [ "$LAG" -le 3 ]; then ST="!!(实际数据滞后 ${LAG} 天, 可 backfill 或检查漏跑)"; WARN=$((WARN+1))
  else ST="XX(滞后 ${LAG} 天)"; ANOM=$((ANOM+1)); fi
  echo "[数据] lastUpdated=$LU_VAL; 实际数据到 $LAST $ST"
fi >> "$LOG"

# ---------- L4 发布(main/gh-pages 数据文件一致) ----------
G1=$(git -C "$DIR" rev-parse "main:data.js" 2>/dev/null)
G2=$(git -C "$DIR" rev-parse "gh-pages:data.js" 2>/dev/null)
if [ -n "$G1" ] && [ "$G1" = "$G2" ]; then
  echo "[发布] main/gh-pages data.js 一致[ok]" >> "$LOG"
else
  echo "[发布] main/gh-pages data.js 不一致[XX] (main=$G1 gh-pages=$G2)" >> "$LOG"
  ANOM=$((ANOM+1))
fi

# ---------- L5 风控 ----------
if [ -f "$DIR/sycm_RISK.txt" ]; then
  echo "[风控] sycm_RISK.txt 熔断标记存在[XX]" >> "$LOG"
  ANOM=$((ANOM+1))
else
  echo "[风控] 无熔断[ok]" >> "$LOG"
fi

# ---------- 汇总 + 通知 ----------
if [ "$ANOM" -eq 0 ] && [ "$WARN" -eq 0 ]; then
  echo "  全部正常[ok]" >> "$LOG"
else
  echo "  异常:${ANOM} 提示:${WARN} (明细见上)" >> "$LOG"
  if [ "$ANOM" -gt 0 ]; then
    notify "巡检发现 ${ANOM} 项异常(调度/服务/数据/发布/风控), 详见 health_check.log"
    exit 1
  fi
fi
exit 0
