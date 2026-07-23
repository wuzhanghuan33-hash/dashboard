#!/bin/bash
# 每日数据更新包装脚本 — 由 cron/launchd 调用
DIR="/Users/luoxiaomin/.local/share/dashboard"
LOG="$DIR/daily_pull.log"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') =====" >> "$LOG"
cd "$DIR" || exit 1

# 1. 拉数据
/usr/bin/python3 daily_pull.py >> "$LOG" 2>&1

# 2. 补剔退同比
/usr/bin/python3 backfill_yoy_net.py >> "$LOG" 2>&1

# 3. 生成 data.js
/usr/bin/python3 fix_data.py >> "$LOG" 2>&1

# 4. 推送到 GitHub Pages
git add data.json data.js index.html
git diff --cached --quiet || {
  git commit -m "auto: $(date '+%Y-%m-%d %H:%M') 数据更新" >> "$LOG" 2>&1
  # GitHub 走 ssh 无需代理
  git push origin main >> "$LOG" 2>&1
  # 同步到 gh-pages 分支
  git checkout gh-pages
  git checkout main -- data.json data.js index.html
  git commit -m "auto: $(date '+%Y-%m-%d %H:%M') 同步" >> "$LOG" 2>&1
  git push origin gh-pages >> "$LOG" 2>&1
  git checkout main
}

# 5. 日志超过 1MB 时截断
if [ $(stat -f%z "$LOG") -gt 1048576 ]; then
    tail -100 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
fi
