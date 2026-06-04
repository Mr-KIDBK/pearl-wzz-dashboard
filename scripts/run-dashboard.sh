#!/usr/bin/env bash
# 启动网页看板 dashboard.py(读 .env 拿 API key, 给暂停后拉起进程用)
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
exec python3 dashboard.py
