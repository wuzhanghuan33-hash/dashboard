#!/bin/bash
# 拉起本地看板服务 server.py(localhost:8080)。幂等:已在响应则不动;端口被僵死进程占则先清。供 health_check.sh 自愈调用。
DIR="/Users/luoxiaomin/.local/share/dashboard"
LOG="$DIR/server.log"

if curl -s -o /dev/null -w "%{http_code}" --max-time 3 http://localhost:8080/ 2>/dev/null | grep -q 200; then
  exit 0  # 已在服务
fi

if lsof -ti tcp:8080 >/dev/null 2>&1; then
  pkill -f "server\.py" 2>/dev/null
  sleep 1
fi

cd "$DIR" || exit 1
nohup /usr/bin/python3 server.py >> "$LOG" 2>&1 &
SERVER_PID=$!
sleep 2

if curl -s -o /dev/null -w "%{http_code}" --max-time 3 http://localhost:8080/ 2>/dev/null | grep -q 200; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] server.py 已拉起 (pid ${SERVER_PID})"
  exit 0
fi
echo "[$(date '+%Y-%m-%d %H:%M:%S')] server.py 启动失败, 见 server.log"
exit 1
