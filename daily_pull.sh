#!/bin/bash
# 每日数据更新包装脚本 — 由 launchd 调用
DIR="/Users/luoxiaomin/Documents/知识库/1-工作/天猫运营/万家乐官旗业绩/dashboard"
LOG="$DIR/daily_pull.log"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') =====" >> "$LOG"
cd "$DIR" && python3 daily_pull.py >> "$LOG" 2>&1
echo "" >> "$LOG"

# 日志超过 1MB 时截断
if [ $(stat -f%z "$LOG") -gt 1048576 ]; then
    tail -100 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
fi
