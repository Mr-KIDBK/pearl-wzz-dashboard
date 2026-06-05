#!/usr/bin/env bash
# 扫描所有账号 config(config.<平台>[-N].json), 按 enabled 起 sniper + 网页看板。无需 byobu。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
set -a; [ -f .env ] && . ./.env; set +a

std_var_for() {  # 平台 → sniper 期望的标准 key 环境变量名
  case "$1" in
    vast) echo VAST_API_KEY;;
    runpod) echo RUNPOD_API_KEY;;
    tensordock) echo TENSORDOCK_API_TOKEN;;
    salad) echo SALAD_API_KEY;;
    *) echo "";;
  esac
}

start_account() {  # $1=platform(salad/runpod/...)  $2=account_id(salad/salad-2/...)
  local platform="$1" acct="$2"
  local cfg="configs/config.${acct}.json"
  if ! python3 -c "import json,sys;sys.exit(0 if json.load(open('$cfg')).get('$platform',{}).get('enabled') else 1)" 2>/dev/null; then
    echo "  ⏭  ${acct} 未启用(${platform}.enabled=false), 跳过"; return
  fi
  if pgrep -f "sniper.py --config $cfg" >/dev/null 2>&1; then
    echo "  ⏭  ${acct} 已在运行, 跳过"; return
  fi
  local std; std="$(std_var_for "$platform")"
  if [ -z "$std" ]; then echo "  ⚠  ${acct} 未知平台(${platform}), 跳过"; return; fi
  local key_env; key_env="$(python3 -c "import json;print(json.load(open('$cfg')).get('api_key_env') or '')" 2>/dev/null)"
  [ -z "$key_env" ] && key_env="$std"
  local key_val="${!key_env:-}"
  if [ -z "$key_val" ]; then echo "  ⚠  ${acct}: ${key_env} 在 .env 为空/未设置, 跳过"; return; fi
  # 关键: 把账号 key 注入成标准变量名, 直接调 python3 sniper.py(不经过会 source .env 覆盖 key 的 run-*.sh)
  env "$std=$key_val" SNIPER_LOG_PATH="logs/${acct}.log" SNIPER_STATE_PATH="state.${acct}.json" \
    nohup python3 sniper.py --config "$cfg" --live >/dev/null 2>>"logs/${acct}.log" </dev/null &
  echo "  ✅ ${acct} 启动 (pid $!, key=${key_env}) → logs/${acct}.log"
}

echo "启动 sniper(扫描账号 config):"
for cfg in configs/config.*.json; do
  [ -e "$cfg" ] || continue
  case "$(basename "$cfg")" in *.example.json) continue;; esac
  acct="$(basename "$cfg" .json)"; acct="${acct#config.}"   # salad / salad-2 / runpod / runpod-2
  platform="${acct%%-*}"                                    # salad-2 → salad
  start_account "$platform" "$acct"
done

echo "启动网页看板:"
if pgrep -f "dashboard.py" >/dev/null 2>&1; then
  echo "  ⏭  dashboard 已在运行"
else
  nohup bash scripts/run-dashboard.sh >>logs/dashboard.log 2>&1 </dev/null &
  echo "  ✅ dashboard 启动 (pid $!) → logs/dashboard.log"
fi

PORT="$(grep -E '^DASHBOARD_PORT=' .env 2>/dev/null | cut -d= -f2)"; PORT="${PORT:-8787}"
echo
echo "✅ 全部启动完成。"
echo "   网页看板: http://<本机IP>:${PORT}  (登录 admin / .env 里的 DASHBOARD_PASSWORD)"
echo "   看日志:   tail -f logs/<账号>.log   (账号: salad / salad-2 / runpod / runpod-2)"
echo "   全部停止: bash scripts/stop-all.sh"
