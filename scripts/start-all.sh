#!/usr/bin/env bash
# 一条命令后台启动全部服务: 4 平台 sniper + 网页看板。无需 byobu, 输出到 logs/。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
set -a; [ -f .env ] && . ./.env; set +a

start_sniper() {  # $1 = 平台名
  local name="$1"
  if pgrep -f "config.${name}.json" >/dev/null 2>&1; then
    echo "  ⏭  ${name} 已在运行, 跳过"; return
  fi
  SNIPER_LOG_PATH="logs/${name}.log" SNIPER_STATE_PATH="state.${name}.json" \
    nohup bash "scripts/run-${name}.sh" --live >/dev/null 2>>"logs/${name}.log" </dev/null &
  echo "  ✅ ${name} 启动 (pid $!) → logs/${name}.log"
}

echo "启动平台 sniper(live 抢卡):"
for p in vast runpod tensordock; do start_sniper "$p"; done

# Salad 仅在 config.salad.json 的 salad.enabled=true 时启动
if [ -f configs/config.salad.json ] && \
   python3 -c "import json,sys;sys.exit(0 if json.load(open('configs/config.salad.json')).get('salad',{}).get('enabled') else 1)" 2>/dev/null; then
  start_sniper salad
else
  echo "  ⏭  salad 未启用(config.salad.json 的 salad.enabled=false), 跳过"
fi

echo "启动网页看板:"
if pgrep -f "python3 dashboard.py" >/dev/null 2>&1; then
  echo "  ⏭  dashboard 已在运行"
else
  nohup bash scripts/run-dashboard.sh >>logs/dashboard.log 2>&1 </dev/null &
  echo "  ✅ dashboard 启动 (pid $!) → logs/dashboard.log"
fi

PORT="$(grep -E '^DASHBOARD_PORT=' .env 2>/dev/null | cut -d= -f2)"; PORT="${PORT:-8787}"
echo
echo "✅ 全部启动完成。"
echo "   网页看板: http://<本机IP>:${PORT}  (登录 admin / .env 里的 DASHBOARD_PASSWORD)"
echo "   看日志:   tail -f logs/<平台>.log   或在看板「配置」页点「查看后台日志」"
echo "   全部停止: bash scripts/stop-all.sh"
