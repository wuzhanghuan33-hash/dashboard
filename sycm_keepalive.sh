#!/bin/bash
# 生意参谋 headless 窗口保活：9225 不在则自动拉起 + 注入 cookie 恢复登录
# launchd 每 5 分钟跑一次；也手动跑: bash sycm_keepalive.sh
# 返回 0=在线/已恢复, 1=失败(需要人工重新登录)
#
# 失败处理策略（2026-09-15 改）：cookie 真的过期时，注入会无限失败——旧版每 5 分钟
# 刷两行日志、不升级、不告警，把故障藏了 4 天(日志涨到 1.26MB)。现在：
#   连续失败达 FAIL_THRESHOLD → 升级告警一次(走 alert.sh，飞书可送达) 并进入退避，
#   退避期间静默跳过（不刷日志、不做无谓请求），恢复后自动复位并回一条恢复通知。
DIR="/Users/luoxiaomin/.local/share/dashboard"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE="$DIR/chrome-profile-sycm"
COOKIES="$DIR/sycm_cookies.json"
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
URL="https://sycm.taobao.com/portal/home.htm"
LOG="$DIR/sycm_keepalive.log"
PORT=9225
STATE="$DIR/.keepalive_state"
FAIL_THRESHOLD=3        # 连续失败达 3 次(约 15 分钟)即告警
BACKOFF_SECONDS=1800    # 告警后退避到 30 分钟才再试，避免无谓重试

log(){ echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG"; }
alert(){ bash "$DIR/alert.sh" "$1" "$2" "$3"; }

read_state(){
  count=0; alerted=0; last_attempt=0
  [ -f "$STATE" ] || return 0
  local v
  v=$(sed -n 's/^count=//p' "$STATE" | head -1);        count=${v:-0}
  v=$(sed -n 's/^alerted=//p' "$STATE" | head -1);      alerted=${v:-0}
  v=$(sed -n 's/^last_attempt=//p' "$STATE" | head -1); last_attempt=${v:-0}
}
write_state(){ printf 'count=%s\nalerted=%s\nlast_attempt=%s\n' "$count" "$alerted" "$last_attempt" > "$STATE"; }

# 登录正常：复位计数；若此前告过警，回一条恢复通知
mark_ok(){
  read_state
  if [ "$count" != "0" ] || [ "$alerted" = "1" ]; then
    log "登录态恢复正常（此前连续失败 ${count} 次）"
    if [ "$alerted" = "1" ]; then
      alert ok "生意参谋登录态已恢复" "自动注入已重新生效，sycm 数据写入恢复正常。"
    fi
  fi
  count=0; alerted=0; last_attempt=0; write_state
  exit 0
}

# 失败：累计计数，到阈值升级告警一次
mark_fail(){
  read_state
  count=$((count+1)); last_attempt=$(date +%s)
  if [ "$count" -ge "$FAIL_THRESHOLD" ] && [ "$alerted" = "0" ]; then
    alert XX "生意参谋登录态失效（连续 ${count} 次自动恢复失败）" \
"原因: $1
需人工扫码重登: bash $DIR/login_tmall.sh
恢复前 sycm 昨日数据将停止写入飞书，看板数据会逐渐滞后。"
    alerted=1
    log "连续失败 ${count} 次，已升级告警并进入 ${BACKOFF_SECONDS}s 退避"
  elif [ "$count" = "1" ]; then
    log "登录恢复失败（第 1 次，原因: $1），累计 ${FAIL_THRESHOLD} 次将告警"
  fi
  write_state
}

# 退避中？是则本轮静默跳过
should_backoff(){
  read_state
  [ "$count" -ge "$FAIL_THRESHOLD" ] || return 1
  local now; now=$(date +%s)
  [ $((now - last_attempt)) -lt "$BACKOFF_SECONDS" ]
}

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
  cd "$DIR" || exit 1
  # 登录态 + 指标块双重确认（URL 显示已登录但接口鉴权可能已静默失效）
  if /usr/bin/python3 -c "
import sys; sys.path.insert(0,'.')
import sycm_extract
print('1' if (sycm_extract.is_logged_in() and sycm_extract.is_metric_readable()) else '0')
" 2>/dev/null | grep -q 1; then
    mark_ok
  fi
  should_backoff && exit 0

  log "登录态不可用，尝试注入 cookie"
  if node "$DIR/sycm_inject_cookies.cjs" "$COOKIES" "$PORT" >/dev/null 2>&1; then
    mark_ok
  fi
  mark_fail "在线但注入 cookie 后仍不可读（cookie 可能已过期）"
  exit 1
fi

# 2. 9225 不在线: 彻底清理残留 profile 进程后 headless 拉起
should_backoff && exit 0
log "9225 不在线，彻底清理残留进程后拉起 headless"
pkill -9 -f "chrome-profile-sycm" 2>/dev/null
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

for i in $(seq 1 10); do
  sleep 3
  if curl -s -m 3 "http://localhost:$PORT/json/version" >/dev/null 2>&1; then break; fi
done
sleep 5

if node "$DIR/sycm_inject_cookies.cjs" "$COOKIES" "$PORT" >/dev/null 2>&1; then
  mark_ok
fi
mark_fail "headless 拉起后注入 cookie 仍失败（cookie 可能过期）"
exit 1
