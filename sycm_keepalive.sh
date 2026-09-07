#!/bin/bash
# 生意参谋 headless 窗口保活：9225 不在则自动拉起 + 注入 cookie 恢复登录
# launchd 每 5 分钟跑一次；也手动跑: bash sycm_keepalive.sh
# 返回 0=在线/已恢复, 1=失败(需要人工重新登录)
DIR="/Users/luoxiaomin/.local/share/dashboard"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE="$DIR/chrome-profile-sycm"
COOKIES="$DIR/sycm_cookies.json"
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
URL="https://sycm.taobao.com/portal/home.htm"
LOG="$DIR/sycm_keepalive.log"
PORT=9225

log(){ echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG"; }

# 0. 风控熔断检查：标记存在 → 保持浏览器关闭，不拉起；每 15 分钟重复提醒一次
if [ -f "$DIR/sycm_RISK.txt" ]; then
  /usr/bin/python3 "$DIR/sycm_risk_check.py" --remind
  log "风险熔断中，跳过保活（浏览器保持关闭）。确认安全后: rm $DIR/sycm_RISK.txt"
  exit 0
fi
# 正常路径也先检测一次风控信号：命中则保持关闭并退出（不让 keepalive 把它拉起来）
if ! /usr/bin/python3 "$DIR/sycm_risk_check.py" --check; then
  log "检测到风控信号，保持浏览器关闭并告警"
  exit 1
fi

# 1. 9225 在线?
if curl -s -m 3 "http://localhost:$PORT/json/version" >/dev/null 2>&1; then
  # 在线: 检查页面是否登录态 + 核心指标是否可读
  cd "$DIR" || exit 1
  if /usr/bin/python3 -c "
import sys; sys.path.insert(0,'.')
import sycm_extract
print('1' if sycm_extract.is_logged_in() else '0')
" 2>/dev/null | grep -q 1; then
    # URL 显示已登录，但再探测指标块是否真能读到数据
    if /usr/bin/python3 -c "
import sys; sys.path.insert(0,'.')
import sycm_extract
print('1' if sycm_extract.is_metric_readable() else '0')
" 2>/dev/null | grep -q 1; then
      exit 0  # 已登录且指标可读，无事可做
    fi
    # 登录态静默失效（页面停在缓存 home.htm，接口鉴权过期）：尝试注入 cookie
    log "已登录但指标读不到（登录态可能静默失效），尝试注入 cookie"
  fi
  # 在线但未登录（登录态丢了）：尝试注入 cookie
  log "在线但未登录，尝试注入 cookie"
  if node "$DIR/sycm_inject_cookies.cjs" "$COOKIES" "$PORT" >/dev/null 2>&1; then
    log "cookie 注入成功，登录恢复"
    exit 0
  fi
  log "cookie 注入失败，可能需要人工重新登录"
  exit 1
fi

# 2. 9225 不在线: 彻底清理残留 profile 进程后 headless 拉起
log "9225 不在线，彻底清理残留进程后拉起 headless"
pkill -9 -f "chrome-profile-sycm" 2>/dev/null
# 连根清理：杀所有相关 Chrome 子进程（GPU/network/renderer），避免半死进程踩踏
pkill -9 -f "user-data-dir=$PROFILE" 2>/dev/null
sleep 4

"$CHROME" \
  --user-data-dir="$PROFILE" \
  --remote-debugging-port="$PORT" \
  --user-agent="$UA" \
  --disable-blink-features=AutomationControlled \
  --no-first-run \
  --no-default-browser-check \
  --headless=new \
  --disable-gpu \
  --renderer-process-limit=2 \
  --window-size=1280,800 \
  --app="$URL" \
  >> "$LOG" 2>&1 &

# 等浏览器起来
for i in $(seq 1 10); do
  sleep 3
  if curl -s -m 3 "http://localhost:$PORT/json/version" >/dev/null 2>&1; then
    break
  fi
done

# 注入 cookie 恢复登录
sleep 5
if node "$DIR/sycm_inject_cookies.cjs" "$COOKIES" "$PORT" >/dev/null 2>&1; then
  log "headless 拉起 + cookie 注入成功"
  exit 0
else
  log "headless 拉起但 cookie 注入失败（cookie 可能过期）→ 需人工重新登录"
  exit 1
fi
