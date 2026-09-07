#!/bin/bash
# 生意参谋昨日数据 → 飞书当月表 自动写回（launchd 入口，月份见 sycm_sync.py TARGET_MONTHS）
# 每天 09:30/14:30 触发，先随机延迟 0~17 分钟再执行（窗口内抖动，防机器特征）
# 实际执行落 09:30-09:47 / 14:30-14:47，仍早于 daily_pull(10:00/15:00)
DIR="/Users/luoxiaomin/.local/share/dashboard"
LOG="$DIR/sycm_sync.log"

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
cd "$DIR" || exit 1
/usr/bin/python3 sycm_sync.py >> "$LOG" 2>&1
echo "exit=$?" >> "$LOG"

# 日志超过 1MB 截断
if [ -f "$LOG" ] && [ $(stat -f%z "$LOG") -gt 1048576 ]; then
    tail -100 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
fi
