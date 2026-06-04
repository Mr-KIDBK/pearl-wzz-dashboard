#!/usr/bin/env bash
# 在 byobu 会话里开窗口, 分别 live 抢 vast / runpod / tensordock (+ salad 若启用)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
SESSION="${SNIPER_SESSION:-sniper}"

command -v byobu >/dev/null 2>&1 || { echo "byobu 未安装" >&2; exit 1; }

if byobu has-session -t "$SESSION" 2>/dev/null; then
  echo "会话 '$SESSION' 已存在。"
  echo "  附加: byobu attach -t $SESSION"
  echo "  重建: byobu kill-session -t $SESSION && $0"
  exit 0
fi
mkdir -p logs

VAST="SNIPER_LOG_PATH=logs/vast.log SNIPER_STATE_PATH=state.vast.json bash scripts/run-vast.sh --live"
RUNPOD="SNIPER_LOG_PATH=logs/runpod.log SNIPER_STATE_PATH=state.runpod.json bash scripts/run-runpod.sh --live"
TENSORDOCK="SNIPER_LOG_PATH=logs/tensordock.log SNIPER_STATE_PATH=state.tensordock.json bash scripts/run-tensordock.sh --live"
SALAD="SNIPER_LOG_PATH=logs/salad.log SNIPER_STATE_PATH=state.salad.json bash scripts/run-salad.sh --live"

# 进程退出后保留窗口为交互 shell, 方便看最后日志 / 按 ↑ 重跑
wrap() { printf "cd %q; %s; ec=\$?; echo; echo \"[exited code=\$ec] 窗口保留, 按 ↑ 重跑\"; exec bash" "$ROOT" "$1"; }

# Salad 仅在 config.salad.json 里 salad.enabled=true 时才开窗口(默认休眠则跳过)
salad_enabled=false
if [ -f configs/config.salad.json ]; then
  if python3 -c "import json,sys; sys.exit(0 if json.load(open('configs/config.salad.json')).get('salad',{}).get('enabled') else 1)" 2>/dev/null; then
    salad_enabled=true
  fi
fi

byobu new-session -d -s "$SESSION" -n vast       "$(wrap "$VAST")"
byobu new-window     -t "$SESSION" -n runpod     "$(wrap "$RUNPOD")"
byobu new-window     -t "$SESSION" -n tensordock "$(wrap "$TENSORDOCK")"
started="vast / runpod / tensordock"
if [ "$salad_enabled" = true ]; then
  byobu new-window   -t "$SESSION" -n salad      "$(wrap "$SALAD")"
  started="$started / salad"
fi
byobu select-window  -t "$SESSION:vast"

echo "✅ 已在 byobu 会话 '$SESSION' 启动平台 ($started)"
[ "$salad_enabled" = false ] && echo "   (salad 未启用: config.salad.json 的 salad.enabled=false, 已跳过)"
echo
echo "  附加查看:  byobu attach -t $SESSION"
echo "  切换窗口:  F3 / F4   (或 Ctrl-a n / p)"
echo "  脱离后台:  F6        (或 Ctrl-a d)"
echo "  停某平台:  切到窗口按 Ctrl-c"
echo "  全部停止:  byobu kill-session -t $SESSION"
