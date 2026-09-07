#!/bin/bash
# 启动 9226 买家号 Chrome（headless，复用 chrome-profile-tmall-buyer 登录态，不清 profile）
# 对比 login_tmall_buyer.sh：那个是一次性扫码登录用，会清 profile；本脚本用于日常复用
set -e
DIR="/Users/luoxiaomin/.local/share/dashboard"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE="$DIR/chrome-profile-tmall-buyer"
PORT=9226

# 若已活着则直接退出（幂等）
if curl -s -m 2 "http://localhost:$PORT/json/version" >/dev/null 2>&1; then
  echo "9226 已在运行"
  exit 0
fi

mkdir -p "$PROFILE"
echo "启动 9226 买家 Chrome（headless，复用登录态）..."
"$CHROME" --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE" \
  --no-first-run --no-default-browser-check --mute-audio \
  --headless=new \
  --disable-gpu --hide-scrollbars \
  --window-size=1440,900 \
  about:blank &
echo "PID: $!"
for i in $(seq 1 15); do
  sleep 1
  if curl -s -m 2 "http://localhost:$PORT/json/version" >/dev/null 2>&1; then
    echo "9226 就绪"
    exit 0
  fi
done
echo "启动超时" >&2
exit 1
