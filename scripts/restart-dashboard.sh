#!/usr/bin/env bash
# 安全重启网页看板: 杀旧 → 等端口真正释放 → 起新 → 验证监听。
# 避免 "pkill; 立刻 start" 抢端口导致的 OSError: [Errno 48] Address already in use(新实例崩溃 → 打不开)。
# 典型用途: 跑完 `uv run python salad_login.py` 后, 用本脚本重启 dashboard 让它重新扫描 secrets/ 会话、激活 salad GPU/余额。
set -uo pipefail
cd "$(dirname "$0")/.."
PORT="$(grep -E '^DASHBOARD_PORT=' .env 2>/dev/null | cut -d= -f2)"; PORT="${PORT:-8787}"

echo "🛑 停止旧 dashboard..."
pkill -f "[d]ashboard.py" 2>/dev/null || true

echo "⏳ 等端口 $PORT 释放(最多 10s)..."
for _ in $(seq 1 20); do
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
  sleep 0.5
done
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "⚠ 端口仍被占用, 强制 kill -9 占用者..."
  lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN | xargs kill -9 2>/dev/null || true
  sleep 1
fi

echo "🚀 启动新 dashboard(uv)..."
mkdir -p logs
nohup bash scripts/run-dashboard.sh >>logs/dashboard.log 2>&1 </dev/null &
sleep 2
if curl -s -o /dev/null --max-time 6 "http://127.0.0.1:$PORT/"; then
  echo "✅ dashboard 已重启, 监听 http://<本机IP>:$PORT (本机 http://localhost:$PORT)"
else
  echo "⚠ 起后未立即响应, 看 logs/dashboard.log 末尾"
  tail -n 5 logs/dashboard.log
fi
