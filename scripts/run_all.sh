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

mkdir -p ./data/logs
python -m scripts.init_db

# Enable or disable ingestors in config.yaml under sources.<name>.enabled.
# This script checks config at startup and launches only enabled ingestors.
is_enabled() {
  python - "$1" <<'PY'
import sys
from backend.config import load_config
name = sys.argv[1]
cfg = load_config()
print("yes" if cfg.get("sources", {}).get(name, {}).get("enabled", False) else "no")
PY
}

start_bg() {
  name="$1"
  shift
  log="./data/logs/${name}.log"
  if pgrep -f "$*" >/dev/null 2>&1; then
    echo "$name already running"
    return
  fi
  echo "starting $name"
  nohup "$@" >> "$log" 2>&1 &
}

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

start_bg api uvicorn backend.main:app --host "$HOST" --port "$PORT"

if [ "$(is_enabled aprs)" = "yes" ]; then
  start_bg aprs python -m backend.ingestors.aprs_direwolf
fi

if [ "$(is_enabled adsb)" = "yes" ]; then
  start_bg adsb python -m backend.ingestors.adsb_readsb
fi

if [ "$(is_enabled satellite)" = "yes" ]; then
  start_bg satellite python -m backend.ingestors.satdump_watcher
fi

echo "RFLens is running on ${HOST}:${PORT}; open http://localhost:${PORT}/ui or your node hostname."
echo "Logs are in ./data/logs/"
