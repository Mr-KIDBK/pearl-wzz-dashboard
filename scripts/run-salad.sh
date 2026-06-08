#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
unset VIRTUAL_ENV 2>/dev/null || true   # 让 uv 干净使用项目 .venv
exec uv run python sniper.py --config configs/config.salad.json "$@"
