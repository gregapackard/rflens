#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT_DIR="$(pwd -P)"

if [ -f "venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "venv/bin/activate"
elif [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

PYTHON_BIN="${PYTHON_BIN:-python}"

repo_pids_for_pattern() {
  pattern="$1"
  pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
  for pid in $pids; do
    [ "$pid" = "$$" ] && continue
    cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
    if [ "$cwd" = "$ROOT_DIR" ]; then
      echo "$pid"
    fi
  done
}

print_process_status() {
  label="$1"
  pattern="$2"
  pids="$(repo_pids_for_pattern "$pattern")"
  if [ -z "$pids" ]; then
    echo "$label: not running"
    return
  fi
  echo "$label: running"
  for pid in $pids; do
    cmd="$(ps -p "$pid" -o args= 2>/dev/null || true)"
    echo "  pid $pid: $cmd"
  done
}

echo "RFLens manual process status"
echo "repo: $ROOT_DIR"
echo

if command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  "$PYTHON_BIN" - <<'PY' || true
from backend.config import load_config
cfg = load_config()
server = cfg.get("server", {}) or {}
host = server.get("host") or "0.0.0.0"
port = server.get("port") or 8080
print(f"configured server: {host}:{port}")
print(f"local UI: http://localhost:{port}/ui")
PY
else
  echo "configured server: unknown (Python not available)"
fi

echo
print_process_status "API" "uvicorn backend.main:app"
print_process_status "APRS ingestor" "python.*-m backend.ingestors.aprs_direwolf"
print_process_status "ADS-B ingestor" "python.*-m backend.ingestors.adsb_readsb"
print_process_status "satellite watcher" "python.*-m backend.ingestors.satdump_watcher"

echo
if [ -d "./data/logs" ] && find ./data/logs -type f -print -quit | grep -q .; then
  echo "recent logs:"
  find ./data/logs -type f -printf "  %TY-%Tm-%Td %TH:%TM %p\n" 2>/dev/null | sort -r | head -10
else
  echo "recent logs: none found under ./data/logs"
fi
