#!/bin/bash
# 每日数据更新包装脚本 — 由 cron/launchd 调用
DIR="/Users/luoxiaomin/.local/share/dashboard"
LOG="$DIR/daily_pull.log"

# 统一告警入口(alert.sh)：持久日志 + 飞书送达 + macOS 兜底。
# 注意 macOS 通知在 launchd 环境会被系统静默丢弃，不能作主通道。
notify() {
  bash "$DIR/alert.sh" XX "看板自动任务" "$1"
}

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
  notify "GitHub $branch 推送重试耗尽仍失败, 见 daily_pull.log"
  return 1
}

# 用 git plumbing 把产物提交到 gh-pages，全程不切分支、不动工作区。
# 为什么不用 checkout：gh-pages 里冻着一份旧脚本快照(33 个 .py/.sh)，`git checkout
# gh-pages` 会把工作区的脚本全部换成旧版；若 launchd 定时任务(keepalive 每 5 分钟)
# 恰好落在窗口内，就会跑旧代码——2026-09-15 10:06 即因此用旧词表误判风控、熔断关停
# 9225，导致当天批量误报。plumbing 只读对象库、写远程 ref，窗口从根上消失。
publish_gh_pages() {
  local parent tree commit idx f blob i delay
  # 父提交优先取远程 tip(避免本地 ref 落后 → non-fast-forward)，拿不到退回本地 ref
  git fetch origin gh-pages >> "$LOG" 2>&1 || true
  parent=$(git rev-parse origin/gh-pages 2>/dev/null || git rev-parse refs/heads/gh-pages 2>/dev/null) || {
    echo "⚠ 无 gh-pages 参照，跳过同步" >> "$LOG"; return 1; }

  idx=$(mktemp -u "${TMPDIR:-/tmp}/ghidx.XXXXXX") || return 1
  if ! GIT_INDEX_FILE="$idx" git read-tree "$parent" >> "$LOG" 2>&1; then
    rm -f "$idx"; echo "⚠ read-tree gh-pages 失败，跳过同步" >> "$LOG"; return 1
  fi
  for f in data.json data.js index.html; do
    if ! blob=$(git rev-parse "main:$f" 2>/dev/null); then
      rm -f "$idx"; echo "⚠ 取 main:$f 失败，跳过同步" >> "$LOG"; return 1
    fi
    if ! GIT_INDEX_FILE="$idx" git update-index --add --cacheinfo "100644,$blob,$f" >> "$LOG" 2>&1; then
      rm -f "$idx"; echo "⚠ update-index $f 失败，跳过同步" >> "$LOG"; return 1
    fi
  done
  tree=$(GIT_INDEX_FILE="$idx" git write-tree 2>/dev/null)
  rm -f "$idx"
  [ -n "$tree" ] || { echo "⚠ write-tree 失败，跳过同步" >> "$LOG"; return 1; }

  # 产物与 gh-pages 现内容一致 → 不造空提交
  if [ "$tree" = "$(git rev-parse "${parent}^{tree}" 2>/dev/null)" ]; then
    echo "gh-pages 产物无变化，跳过提交" >> "$LOG"; return 0
  fi

  commit=$(git commit-tree "$tree" -p "$parent" -m "auto: $(date '+%Y-%m-%d %H:%M') 同步" 2>>"$LOG") || {
    echo "⚠ commit-tree 失败，跳过同步" >> "$LOG"; return 1; }

  # 直推 commit 到远程 gh-pages(不碰任何本地 ref)，成功后再对齐本地 ref
  # 注意用 ${commit} 花括号：zsh 会把 "$commit:r..." 的 :r 当「去扩展名」修饰符吃掉
  for ((i=1; i<=5; i++)); do
    if git push origin "${commit}:refs/heads/gh-pages" >> "$LOG" 2>&1; then
      git update-ref refs/heads/gh-pages "$commit"
      echo "✓ push gh-pages 成功 (第${i}次, 未切分支)" >> "$LOG"
      return 0
    fi
    delay=$((10 * (2 ** (i - 1))))
    echo "⚠ push gh-pages 失败(第${i}/5次)，${delay}s 后重试" >> "$LOG"
    sleep "$delay"
  done
  echo "✗ push gh-pages 重试耗尽仍失败" >> "$LOG"
  notify "GitHub gh-pages 推送重试耗尽仍失败, 见 daily_pull.log"
  return 1
}

echo "===== $(date '+%Y-%m-%d %H:%M:%S') =====" >> "$LOG"
cd "$DIR" || exit 1

# ---- 看门狗: 本轮超时自动终止并通知, 防卡死让 launchd 误以为 job 未结束而阻塞后续定时 ----
WATCHDOG_TIMEOUT=${WATCHDOG_TIMEOUT:-2400}
(
  sleep "$WATCHDOG_TIMEOUT"
  if kill -0 "$$" 2>/dev/null; then
    PGID=$(ps -o pgid= -p "$$" 2>/dev/null | tr -d ' ')
    [ -n "$PGID" ] || PGID="$$"
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') [看门狗] 执行超时 ${WATCHDOG_TIMEOUT}s, 终止进程组 ${PGID} =====" >> "$LOG"
    notify "数据任务超时已自动终止, 将等待下次定时补跑"
    kill -TERM -- "-${PGID}" 2>/dev/null
    sleep 8
    kill -KILL -- "-${PGID}" 2>/dev/null
  fi
) &
WD_PID=$!
trap "kill $WD_PID 2>/dev/null" EXIT

# 1. 拉数据
/usr/bin/python3 daily_pull.py >> "$LOG" 2>&1 || {
  RC=$?
  echo "✗ daily_pull.py 失败 (exit=$RC)" >> "$LOG"
  notify "数据拉取失败(exit=$RC), 本轮中止, 下轮定时重试"
  exit $RC
}

# 2. 补剔退同比
/usr/bin/python3 backfill_yoy_net.py >> "$LOG" 2>&1 || {
  RC=$?
  echo "✗ backfill_yoy_net.py 失败 (exit=$RC)" >> "$LOG"
  notify "剔退同比补算失败(exit=$RC), 本轮中止"
  exit $RC
}

# 3. 生成 data.js（内含同期对齐硬校验：错位直接抛错中止，错位数据不会进入 data.js）
/usr/bin/python3 fix_data.py >> "$LOG" 2>&1 || {
  RC=$?
  echo "✗ fix_data.py 失败 (exit=$RC)" >> "$LOG"
  notify "data.js 生成校验失败(exit=$RC), 未推送, 请检查同期对齐"
  exit $RC
}

# 3.5 更新 index.html 的 data.js 版本号，破浏览器/CDN 静态缓存
#    （否则版本号固定，用户浏览器会一直用缓存的旧 data.js）
VER=$(date '+%Y%m%d%H%M')
sed -i '' "s/data\.js?v=[0-9a-z]*/data.js?v=${VER}/" index.html

# 3.75 数据校验防呆：覆盖率（快，data.json 自检）+ 抽样对账（开飞书读源表比对）。
#   失败不阻断 push，但会打印原因 + macOS 通知（verify_data.py 内部），人工需看 LOG。
/usr/bin/python3 verify_data.py --coverage >> "$LOG" 2>&1 || echo "⚠ coverage 校验异常，见上方输出" >> "$LOG"
/usr/bin/python3 verify_data.py --reconcile >> "$LOG" 2>&1 || echo "⚠ reconcile 对账不一致，见上方输出" >> "$LOG"

# 4. 推送到 GitHub Pages
git add data.json data.js index.html
git diff --cached --quiet || {
  git commit -m "auto: $(date '+%Y-%m-%d %H:%M') 数据更新" >> "$LOG" 2>&1
  # GitHub 走 HTTPS（gh 认证），链路抖动由 push_retry 自动重试
  push_retry main

  # 同步到 gh-pages 分支：plumbing 提交 + 直推，全程不切分支
  # (旧实现用 git checkout gh-pages，会临时把工作区脚本换成 gh-pages 的旧快照，
  #  是 2026-09-15 风控误熔断的根因，已废弃)
  publish_gh_pages
}

# 5. 日志超过 1MB 时截断
if [ $(stat -f%z "$LOG") -gt 1048576 ]; then
    tail -100 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
fi
