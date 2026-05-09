#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f "venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "venv/bin/activate"
elif [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

HOST="${RFLENS_HOST:-}"
if [ -z "$HOST" ]; then
  HOST="$(python - <<'PY'
from backend.config import load_config
cfg = load_config()
print((cfg.get("server", {}) or {}).get("host") or "0.0.0.0")
PY
)"
fi

PORT="${RFLENS_PORT:-}"
if [ -z "$PORT" ]; then
  PORT="$(python - <<'PY'
from backend.config import load_config
cfg = load_config()
print((cfg.get("server", {}) or {}).get("port") or 8080)
PY
)"
fi

uvicorn backend.main:app --host "$HOST" --port "$PORT"
