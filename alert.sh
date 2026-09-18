#!/bin/bash
# 统一告警入口（看板数据管道）。所有告警都从这里走，通道按可靠性排序：
#   1) alert.log        持久日志，必成通道，可事后追溯
#   2) 飞书 webhook      主送达通道（自定义机器人），配好才启用
#   3) osascript 通知    兜底，受通知权限/勿扰影响
#
# 为什么必须有它：告警不能只押在 macOS 通知上。2026-09-15 的 A/B 实测中
# launchd 触发的通知没被看到，一度判定为"后台静默丢弃"；但 2026-09-18 用户确认
# 收到了 launchd 触发的告警横幅，该判定不成立。真实行为受通知权限/勿扰模式影响，
# 且无法远程确认是否投递——所以飞书 webhook 才是主通道，macOS 通知仅兜底。
#
# 用法: bash alert.sh <级别> <标题> [正文]
#   级别: XX=异常需人工  !=提示  ok=恢复
# 飞书配置（可选，未配置则只写日志并在日志里标注）:
#   ~/.local/share/dashboard/feishu_webhook.txt         机器人 webhook 地址
#   ~/.local/share/dashboard/feishu_webhook_secret.txt  签名密钥（开启「签名校验」时）
set -u
DIR="/Users/luoxiaomin/.local/share/dashboard"
ALOG="$DIR/alert.log"
WEBHOOK_FILE="$DIR/feishu_webhook.txt"
SECRET_FILE="$DIR/feishu_webhook_secret.txt"

LEVEL="${1:-XX}"; TITLE="${2:-看板告警}"; BODY="${3:-}"
TS=$(date '+%Y-%m-%d %H:%M:%S')

# 1) 持久日志：任何告警先落地，保证「至少留下痕迹」
{
  printf '[%s] [%s] %s\n' "$TS" "$LEVEL" "$TITLE"
  [ -n "$BODY" ] && printf '        %s\n' "$BODY"
} >> "$ALOG" 2>/dev/null

# 2) 飞书 webhook：主送达通道，失败重试但不阻塞调用方
if [ -s "$WEBHOOK_FILE" ]; then
  A_TITLE="$TITLE" A_BODY="$BODY" A_LEVEL="$LEVEL" \
  python3 - "$WEBHOOK_FILE" "$SECRET_FILE" <<'PY' >> "$ALOG" 2>&1 || true
import base64, hashlib, hmac, json, os, sys, time, urllib.request

wf, sf = sys.argv[1], sys.argv[2]
try:
    url = open(wf).read().strip()
    secret = open(sf).read().strip() if os.path.exists(sf) and os.path.getsize(sf) else ""
except OSError as e:
    print("[feishu] 读配置失败: %s" % e); raise SystemExit(0)

level = os.environ.get("A_LEVEL", "XX")
icon = {"XX": "[XX]", "!!": "[!!]", "ok": "[ok]"}.get(level, "[XX]")
title = os.environ.get("A_TITLE", "")
body = os.environ.get("A_BODY", "")
payload = {"msg_type": "text", "content": {"text": ("%s %s\n%s" % (icon, title, body)).strip()}}
if secret:
    ts = str(int(time.time()))
    s = "%s\n%s" % (ts, secret)
    payload["timestamp"] = ts
    payload["sign"] = base64.b64encode(
        hmac.new(s.encode("utf-8"), digestmod=hashlib.sha256).digest()).decode()

data = json.dumps(payload).encode("utf-8")
for attempt in range(1, 4):
    try:
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read().decode("utf-8"))
        if resp.get("code") == 0:
            print("[feishu] 告警送达 ok (第%d次)" % attempt)
            break
        print("[feishu] 送达被拒: %s" % resp)
    except Exception as e:
        print("[feishu] 发送异常(第%d/3次): %s" % (attempt, e))
    time.sleep(3)
PY
else
  echo "[feishu] 未配置 webhook → 本告警只写日志，未送达手机" >> "$ALOG" 2>/dev/null
fi

# 3) macOS 通知：launchd(Aqua 会话)下实测可投递，但受通知权限/勿扰影响，仅作兜底
osascript -e "display notification \"${TITLE}\" with title \"看板告警\"" >/dev/null 2>&1
exit 0
