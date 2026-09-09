#!/bin/bash
# 生意参谋昨日数据 → 飞书当月表 自动写回（launchd 入口，月份见 sycm_sync.py TARGET_MONTHS）
# 每天 09:30/14:30 触发，先随机延迟 0~17 分钟再执行（窗口内抖动，防机器特征）
# 实际执行落 09:30-09:47 / 14:30-14:47，仍早于 daily_pull(10:00/15:00)
DIR="/Users/luoxiaomin/.local/share/dashboard"
LOG="$DIR/sycm_sync.log"

# macOS 通知(与 verify_data.py 同机制)
notify() {
  osascript -e "display notification \"$1\" with title \"看板自动任务\"" >/dev/null 2>&1
}

# 风控熔断：标记存在则跳过本次同步（浏览器保持关闭）
if [ -f "$DIR/sycm_RISK.txt" ]; then
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') 风险熔断中，跳过同步 =====" >> "$LOG"
  exit 0
fi
# 正常路径先检测风控信号：命中则中止本次同步
if ! /usr/bin/python3 "$DIR/sycm_risk_check.py" --check; then
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') 检测到风控信号，中止同步 =====" >> "$LOG"
  exit 1
fi

DELAY=$((RANDOM % 1020))
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 随机延迟 ${DELAY}s =====" >> "$LOG"
sleep "$DELAY"

# ---- 看门狗: 真实工作段超时自动终止并通知(随机延迟不计入, 防误杀正常抖动) ----
WATCHDOG_TIMEOUT=${WATCHDOG_TIMEOUT:-2400}
(
  sleep "$WATCHDOG_TIMEOUT"
  if kill -0 "$$" 2>/dev/null; then
    PGID=$(ps -o pgid= -p "$$" 2>/dev/null | tr -d ' ')
    [ -n "$PGID" ] || PGID="$$"
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') [看门狗] 执行超时 ${WATCHDOG_TIMEOUT}s, 终止进程组 ${PGID} =====" >> "$LOG"
    notify "生意参谋同步超时已自动终止, 将等待下次定时补跑"
    kill -TERM -- "-${PGID}" 2>/dev/null
    sleep 8
    kill -KILL -- "-${PGID}" 2>/dev/null
  fi
) &
WD_PID=$!
trap "kill $WD_PID 2>/dev/null" EXIT

cd "$DIR" || exit 1
/usr/bin/python3 sycm_sync.py >> "$LOG" 2>&1
RC=$?
echo "exit=$RC" >> "$LOG"
if [ "$RC" -ne 0 ]; then
  notify "生意参谋同步失败(exit=$RC), 见 sycm_sync.log"
fi

# 日志超过 1MB 截断
if [ -f "$LOG" ] && [ $(stat -f%z "$LOG") -gt 1048576 ]; then
    tail -100 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
fi
