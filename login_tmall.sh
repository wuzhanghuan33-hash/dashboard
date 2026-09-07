#!/bin/bash
# 登录天猫生意参谋并保存登录态 cookie（供 headless 自动复用）
#
# 流程:
#   1. 有头模式打开生意参谋登录页（带 debug 端口 9225）
#   2. 你在弹出的窗口扫码/密码登录
#   3. 登录完成关掉窗口
#   4. 脚本导出登录态 cookie 到 sycm_cookies.json
#
# 之后无需任何手动操作: sycm_keepalive.sh 会以 headless 拉起 + 注入 cookie。
# 仅当 cookie 过期、保活注入失败时才需重跑本脚本。

DIR="/Users/luoxiaomin/.local/share/dashboard"
PROFILE="$DIR/chrome-profile-sycm"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT=9225
# 注意: sycm.taobao.com/custom/login.htm 会 302 到营销页（无登录表单），
# 必须用淘宝统一登录页 login.taobao.com + redirectURL 才会弹出扫码窗口（2026-08-17 实测）
URL="${1:-https://login.taobao.com/member/login.jhtml?redirectURL=https%3A%2F%2Fsycm.taobao.com%2Fportal%2Fhome.htm}"
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"

# 0. 清理可能占用 9225 / profile 的残留进程
pkill -f "chrome-profile-sycm" 2>/dev/null
sleep 3

# 1. 有头模式启动并打开登录页
echo "→ 请在弹出的 Chrome 窗口中登录天猫生意参谋"
echo "  登录完成后【关闭窗口】，脚本会自动导出登录态 cookie"
"$CHROME" \
  --user-data-dir="$PROFILE" \
  --remote-debugging-port="$PORT" \
  --user-agent="$UA" \
  --disable-blink-features=AutomationControlled \
  --no-first-run \
  --no-default-browser-check \
  --window-size=1280,800 \
  --app="$URL"

# 2. Chrome 退出后导出 cookie
sleep 2
if curl -s -m 3 "http://localhost:$PORT/json/version" >/dev/null 2>&1; then
  echo "→ 检测到浏览器仍在线（可能未完全退出），等待 5s 重试..."
  sleep 5
fi

cd "$DIR" || exit 1
if /usr/bin/python3 sycm_save_cookies.py; then
  echo "✓ 登录态 cookie 已保存，后续 headless 自动复用"
else
  echo "⚠ cookie 导出失败，请确认已登录且浏览器已关闭"
fi
