#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="/etc/systemd/system"

sudo install -m 0644 "${SCRIPT_DIR}/systemd/rflens-api.service" "${UNIT_DIR}/rflens-api.service"
sudo install -m 0644 "${SCRIPT_DIR}/systemd/rflens-adsb.service" "${UNIT_DIR}/rflens-adsb.service"
sudo install -m 0644 "${SCRIPT_DIR}/systemd/rflens-aprs.service" "${UNIT_DIR}/rflens-aprs.service"

sudo systemctl daemon-reload
sudo systemctl enable --now rflens-api rflens-adsb

cat <<'EOF'
RF Lens systemd services installed.

Enabled now:
  rflens-api
  rflens-adsb

Optional APRS service installed but not enabled:
  sudo systemctl enable --now rflens-aprs
EOF
