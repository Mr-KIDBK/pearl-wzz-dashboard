#!/usr/bin/env bash
# 停止 start-all.sh 启动的 byobu 抢卡会话
set -euo pipefail
SESSION="${SNIPER_SESSION:-sniper}"

command -v byobu >/dev/null 2>&1 || { echo "byobu 未安装" >&2; exit 1; }

if byobu has-session -t "$SESSION" 2>/dev/null; then
  byobu kill-session -t "$SESSION"
  echo "✅ 已停止会话 '$SESSION' (vast / runpod / tensordock / salad 全部退出)"
else
  echo "会话 '$SESSION' 不存在, 无需停止"
fi

# 兜底: 清理可能游离在会话外的 sniper 进程
left="$(pgrep -af "sniper.py" || true)"
if [ -n "$left" ]; then
  echo
  echo "⚠️  仍检测到游离的 sniper.py 进程:"
  echo "$left"
  echo "如需强杀: pkill -f sniper.py"
fi
