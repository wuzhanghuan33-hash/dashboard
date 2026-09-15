#!/bin/bash
# 扫码重登生意参谋并导出登录态 cookie（供 headless 自动复用）。
#
# 流程：清理占用 → 有头开登录页 → 校验窗口真起来 → 等你扫码 → 导航到生意参谋
#       首页 → 校验指标块真能读到 → 备份旧 cookie → 导出新 cookie
#
# 为什么这么啰嗦（2026-09-15 踩坑）：旧版不校验任何东西——profile 被占用时 Chrome
# 因 SingletonLock 直接 abort、登录窗口根本没出现，旧版却照样执行导出，把一份
# **未登录**的 cookie 覆盖到文件上，把好登录态自毁。现在任一步校验不过就中止，
# 且**绝不动原 cookie**。
set -u
DIR="/Users/luoxiaomin/.local/share/dashboard"
PROFILE="$DIR/chrome-profile-sycm"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
COOKIES="$DIR/sycm_cookies.json"
PORT=9225
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
# sycm.taobao.com/custom/login.htm 会 302 到营销页（无登录表单），必须用淘宝统一登录页
LOGIN_URL="https://login.taobao.com/member/login.jhtml?redirectURL=https%3A%2F%2Fsycm.taobao.com%2Fportal%2Fhome.htm"
SYCM_URL="https://sycm.taobao.com/portal/home.htm"

cd "$DIR" || exit 1
pyc(){ /usr/bin/python3 -c "
import sys; sys.path.insert(0,'.')
import sycm_extract
print('1' if $1 else '0')
" 2>/dev/null | grep -q 1; }

echo "→ 清理可能占用 9225 / profile 的残留进程"
pkill -f "chrome-profile-sycm" 2>/dev/null
sleep 3
# 陈旧 SingletonLock 会让新窗口 abort（chrome 通常自清，死进程残留时不会）
rm -f "$PROFILE"/SingletonLock "$PROFILE"/SingletonCookie "$PROFILE"/SingletonSocket

echo "→ 启动登录窗口…"
"$CHROME" \
  --user-data-dir="$PROFILE" \
  --remote-debugging-port="$PORT" \
  --user-agent="$UA" \
  --disable-blink-features=AutomationControlled \
  --no-first-run \
  --no-default-browser-check \
  --window-size=1280,860 \
  --app="$LOGIN_URL" >/dev/null 2>&1 &
CHROME_PID=$!

# 校验①：窗口真的起来了？（旧版缺这步 → 假成功自毁 cookie）
READY=0
for i in $(seq 1 20); do
  sleep 2
  if curl -s -m 3 "http://localhost:$PORT/json/version" >/dev/null 2>&1; then READY=1; break; fi
  kill -0 "$CHROME_PID" 2>/dev/null || break
done
if [ "$READY" != "1" ]; then
  echo "✗ 登录窗口未能启动（profile 被占用 / SingletonLock 冲突）。"
  echo "  已保留原 cookie，未做任何改动。确认无其他 sycm 窗口后重跑。"
  exit 1
fi

echo "→ 请在弹出的 Chrome 窗口扫码登录（最多等 5 分钟）"
# 校验②：等到真登录态（轮询 URL 不再落在登录页）
LOGGED=0
for i in $(seq 1 150); do
  sleep 2
  if pyc "sycm_extract.is_logged_in()"; then LOGGED=1; break; fi
done
if [ "$LOGGED" != "1" ]; then
  echo "✗ 5 分钟内未检测到登录。已保留原 cookie，未做任何改动。"
  exit 1
fi

# 扫码后常落在商家中心(myseller)，显式导航到生意参谋首页
echo "✓ 检测到登录，导航到生意参谋首页校验…"
node -e '
const http=require("http");
const url=process.argv[1], port=process.argv[2];
http.get("http://localhost:"+port+"/json",r=>{let d="";r.on("data",c=>d+=c);r.on("end",()=>{
const p=JSON.parse(d).filter(t=>t.type==="page")[0];
if(!p){console.log("NO_PAGE");process.exit(1);}
const ws=new WebSocket(p.webSocketDebuggerUrl);let id=0;
const send=(m,pr)=>new Promise(res=>{const i=++id;const h=e=>{const x=JSON.parse(e.data);if(x.id===i){ws.removeEventListener("message",h);res(x);}};ws.addEventListener("message",h);ws.send(JSON.stringify({id:i,method:m,params:pr}));});
ws.onopen=async()=>{await send("Page.enable",{});await send("Page.navigate",{url:url});console.log("NAVIGATED");ws.close();process.exit(0);};
});});
' "$SYCM_URL" "$PORT" >/dev/null 2>&1
sleep 18

# 校验③：指标块真能读到（URL 已登录≠接口鉴权有效，这是最容易骗人的一层）
if ! pyc "sycm_extract.is_metric_readable()"; then
  echo "✗ 登录态校验未通过（核心指标块读不到）。已保留原 cookie，未覆盖。"
  echo "  可稍后重跑本脚本；若反复失败见 $DIR/sycm_keepalive.log"
  exit 1
fi

# 三项校验都过才动 cookie：先备份，再导出
if [ -f "$COOKIES" ]; then
  cp "$COOKIES" "$COOKIES.bak.$(date +%Y%m%d%H%M%S)"
  echo "→ 旧 cookie 已备份为 sycm_cookies.json.bak.*"
fi
if /usr/bin/python3 sycm_save_cookies.py; then
  echo "✓ 登录态已保存并校验通过。可以关闭窗口了（keepalive 会自动接管 headless）。"
else
  echo "✗ cookie 导出失败，原 cookie 已在备份中，请重跑。"
  exit 1
fi
