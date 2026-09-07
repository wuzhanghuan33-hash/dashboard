#!/bin/bash
# 复用 profile 有头启动 9226 买家登录（不清 profile）。登录成功后运行 buyer_save_cookies.py 导出。
# 与 login_tmall_buyer.sh 区别：那个会 rm profile（一次性干净登录），这个保留登录态用于补 cookie。
set -e
DIR="/Users/luoxiaomin/.local/share/dashboard"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE="$DIR/chrome-profile-tmall-buyer"
PORT=9226
LOGIN="https://login.taobao.com/member/login.jhtml"

if curl -s -m 2 "http://localhost:$PORT/json/version" >/dev/null 2>&1; then
  echo "关闭已有 9226 Chrome..."
  pkill -f "remote-debugging-port=$PORT" 2>/dev/null || true
  sleep 2
fi

mkdir -p "$PROFILE"
echo "启动买家登录 Chrome (port $PORT)... 请用【个人买家淘宝号】扫码，勿用商家号。"
echo "登录完成后不要关窗口，回来执行: python3 buyer_save_cookies.py"
"$CHROME" --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE" \
  --no-first-run --no-default-browser-check --mute-audio \
  "$LOGIN" &
echo "PID: $!"
