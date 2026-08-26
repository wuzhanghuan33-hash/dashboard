#!/bin/bash
# 每日数据更新包装脚本 — 由 cron/launchd 调用
DIR="/Users/luoxiaomin/.local/share/dashboard"
LOG="$DIR/daily_pull.log"

# push 带重试：国际链路间歇抖动会偶发超时，最多试 5 次(间隔递增)扛过坏窗口
push_retry() {
  local branch="$1"
  local attempts=5
  local delay=10
  for ((i=1; i<=attempts; i++)); do
    if git push origin "$branch" >> "$LOG" 2>&1; then
      echo "✓ push $branch 成功 (第${i}次)" >> "$LOG"
      return 0
    fi
    echo "⚠ push $branch 失败(第${i}/${attempts}次)，${delay}s 后重试" >> "$LOG"
    sleep "$delay"
    delay=$((delay * 2))
  done
  echo "✗ push $branch 重试耗尽仍失败" >> "$LOG"
  return 1
}

echo "===== $(date '+%Y-%m-%d %H:%M:%S') =====" >> "$LOG"
cd "$DIR" || exit 1

# 1. 拉数据
/usr/bin/python3 daily_pull.py >> "$LOG" 2>&1

# 2. 补剔退同比
/usr/bin/python3 backfill_yoy_net.py >> "$LOG" 2>&1

# 3. 生成 data.js（内含同期对齐硬校验：错位直接抛错中止，错位数据不会进入 data.js）
/usr/bin/python3 fix_data.py >> "$LOG" 2>&1

# 3.5 更新 index.html 的 data.js 版本号，破浏览器/CDN 静态缓存
#    （否则版本号固定，用户浏览器会一直用缓存的旧 data.js）
VER=$(date '+%Y%m%d%H%M')
sed -i '' "s/data\.js?v=[0-9a-z]*/data.js?v=${VER}/" index.html

# 4. 推送到 GitHub Pages
git add data.json data.js index.html
git diff --cached --quiet || {
  git commit -m "auto: $(date '+%Y-%m-%d %H:%M') 数据更新" >> "$LOG" 2>&1
  # GitHub 走 HTTPS（gh 认证），链路抖动由 push_retry 自动重试
  push_retry main

  # 同步到 gh-pages 分支
  # 未提交的代码改动会挡住 `git checkout gh-pages`，先 stash，同步后恢复
  git stash push -m "daily-sync" >> "$LOG" 2>&1 || true
  if git checkout gh-pages >> "$LOG" 2>&1; then
    git checkout main -- data.json data.js index.html
    git commit -m "auto: $(date '+%Y-%m-%d %H:%M') 同步" >> "$LOG" 2>&1
    push_retry gh-pages
    git checkout main >> "$LOG" 2>&1
  else
    echo "⚠ gh-pages 同步失败（checkout gh-pages 出错），下次运行重试" >> "$LOG"
  fi
  if git stash list | grep -q "daily-sync"; then git stash pop >> "$LOG" 2>&1; fi
}

# 5. 日志超过 1MB 时截断
if [ $(stat -f%z "$LOG") -gt 1048576 ]; then
    tail -100 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
fi
