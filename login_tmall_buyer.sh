#!/bin/bash
# 一次性：天猫/淘宝买家版扫码登录（独立 profile，不动 9225 生意参谋卖家会话）
# 重要：请用【个人买家淘宝号】扫码，不要用商家/店铺号
#   - 商家账号会被淘宝识别为卖家身份，拒绝查看别家店铺商品详情
# 用后退出，cookie 存 chrome-profile-tmall-buyer，后续 9226 复用
set -e
DIR="/Users/luoxiaomin/.local/share/dashboard"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE="$DIR/chrome-profile-tmall-buyer"
PORT=9226
LOGIN="https://login.taobao.com/member/login.jhtml"

# 关掉已有 9226（如还在跑），清 profile，重开干净登录
if curl -s -m 2 "http://localhost:$PORT/json/version" >/dev/null 2>&1; then
  echo "关闭已有 9226 Chrome..."
  pkill -f "remote-debugging-port=$PORT" 2>/dev/null || true
  sleep 2
fi
rm -rf "$PROFILE"/* 2>/dev/null || true

mkdir -p "$PROFILE"
echo "启动买家登录 Chrome (port $PORT)... 请用【个人买家淘宝号】扫码，勿用商家号。"
"$CHROME" --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE" \
  --no-first-run --no-default-browser-check --mute-audio \
  "$LOGIN" &
echo "PID: $!"
echo "登录完成后关闭该窗口即可。cookies 已存入 $PROFILE"
