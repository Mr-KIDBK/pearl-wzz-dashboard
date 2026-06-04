#!/usr/bin/env bash
# 一条命令停止全部服务: 4 平台 sniper + 网页看板
set -euo pipefail
SESSION="${SNIPER_SESSION:-sniper}"

command -v byobu >/dev/null 2>&1 || { echo "byobu 未安装" >&2; exit 1; }

if byobu has-session -t "$SESSION" 2>/dev/null; then
  byobu kill-session -t "$SESSION"
  echo "✅ 已停止 byobu 会话 '$SESSION'(vast / runpod / tensordock / salad / dashboard)"
else
  echo "会话 '$SESSION' 不存在"
fi

# 兜底: 清理游离在会话外的 sniper / dashboard 进程
pkill -f "sniper.py --config" 2>/dev/null && echo "  已清理游离 sniper 进程" || true
pkill -f "dashboard.py" 2>/dev/null && echo "  已清理游离 dashboard 进程" || true

echo "✅ 全部服务已停止"
