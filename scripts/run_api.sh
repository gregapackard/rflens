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

PYTHON_BIN="${PYTHON_BIN:-python}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: Python is not available. Run: bash ./scripts/setup.sh" >&2
  exit 1
fi

if ! "$PYTHON_BIN" -c "import fastapi, uvicorn, yaml" >/dev/null 2>&1; then
  echo "ERROR: RFLens Python dependencies are missing. Run: bash ./scripts/setup.sh" >&2
  exit 1
fi

HOST="${RFLENS_HOST:-}"
if [ -z "$HOST" ]; then
  HOST="$("$PYTHON_BIN" - <<'PY'
from backend.config import load_config
cfg = load_config()
print((cfg.get("server", {}) or {}).get("host") or "0.0.0.0")
PY
)"
fi

PORT="${RFLENS_PORT:-}"
if [ -z "$PORT" ]; then
  PORT="$("$PYTHON_BIN" - <<'PY'
from backend.config import load_config
cfg = load_config()
print((cfg.get("server", {}) or {}).get("port") or 8080)
PY
)"
fi

uvicorn backend.main:app --host "$HOST" --port "$PORT"
