#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT_DIR="$(pwd -P)"

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

collect_targets() {
  {
    repo_pids_for_pattern "uvicorn backend.main:app"
    repo_pids_for_pattern "python.*-m backend.ingestors.aprs_direwolf"
    repo_pids_for_pattern "python.*-m backend.ingestors.adsb_readsb"
    repo_pids_for_pattern "python.*-m backend.ingestors.satdump_watcher"
  } | sort -n | uniq
}

targets="$(collect_targets)"

if [ -z "$targets" ]; then
  echo "No manual RFLens processes found for this repo."
  exit 0
fi

echo "Stopping manual RFLens processes for $ROOT_DIR:"
for pid in $targets; do
  cmd="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  echo "  SIGTERM pid $pid: $cmd"
  kill -TERM "$pid" 2>/dev/null || true
done

sleep 2

remaining=""
for pid in $targets; do
  if kill -0 "$pid" 2>/dev/null; then
    remaining="${remaining}${pid} "
  fi
done

if [ -n "$remaining" ]; then
  echo "Still running after SIGTERM: $remaining"
  echo "Review manually before using stronger signals."
else
  echo "Stopped manual RFLens processes."
fi
