#!/usr/bin/env bash
# 一条命令停止全部: 扫描所有账号 config 的 sniper + 网页看板。
set -uo pipefail
cd "$(dirname "$0")/.." 2>/dev/null || true
stopped=0

for cfg in configs/config.*.json; do
  [ -e "$cfg" ] || continue
  case "$(basename "$cfg")" in *.example.json) continue;; esac
  pids="$(pgrep -f "sniper.py --config $cfg" || true)"
  if [ -n "$pids" ]; then kill $pids 2>/dev/null && echo "  🛑 $(basename "$cfg" .json) 已停 (pid $pids)"; stopped=1; fi
done

dpids="$(pgrep -f "dashboard.py" || true)"
if [ -n "$dpids" ]; then kill $dpids 2>/dev/null && echo "  🛑 dashboard 已停 (pid $dpids)"; stopped=1; fi

if [ "$stopped" = 0 ]; then echo "没有在运行的服务"; else echo "✅ 全部已停止"; fi
