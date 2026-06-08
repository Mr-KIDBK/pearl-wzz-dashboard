#!/usr/bin/env bash
# 启动网页看板 dashboard.py(读 .env 拿 API key, 给暂停后拉起进程用)
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
unset VIRTUAL_ENV 2>/dev/null || true   # 让 uv 干净使用项目 .venv(忽略外部 stray 变量)
exec uv run python dashboard.py
