#!/usr/bin/env bash
# 一条命令停止全部服务: 4 平台 sniper + 网页看板。
set -uo pipefail
cd "$(dirname "$0")/.." 2>/dev/null || true
stopped=0

for name in vast runpod tensordock salad; do
  pids="$(pgrep -f "config.${name}.json" || true)"
  if [ -n "$pids" ]; then kill $pids 2>/dev/null && echo "  🛑 ${name} 已停 (pid $pids)"; stopped=1; fi
done

dpids="$(pgrep -f "python3 dashboard.py" || true)"
if [ -n "$dpids" ]; then kill $dpids 2>/dev/null && echo "  🛑 dashboard 已停 (pid $dpids)"; stopped=1; fi

# 向后兼容: 若旧版用 byobu 起过, 也一并停掉
if command -v byobu >/dev/null 2>&1 && byobu has-session -t "${SNIPER_SESSION:-sniper}" 2>/dev/null; then
  byobu kill-session -t "${SNIPER_SESSION:-sniper}" 2>/dev/null && echo "  🛑 已停旧 byobu 会话 '${SNIPER_SESSION:-sniper}'"; stopped=1
fi

if [ "$stopped" = 0 ]; then echo "没有在运行的服务"; else echo "✅ 全部已停止"; fi
