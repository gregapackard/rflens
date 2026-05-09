#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="/etc/systemd/system"

sudo install -m 0644 "${SCRIPT_DIR}/systemd/rflens-api.service" "${UNIT_DIR}/rflens-api.service"
sudo install -m 0644 "${SCRIPT_DIR}/systemd/rflens-adsb.service" "${UNIT_DIR}/rflens-adsb.service"
sudo install -m 0644 "${SCRIPT_DIR}/systemd/rflens-aprs-radio.service" "${UNIT_DIR}/rflens-aprs-radio.service"
sudo install -m 0644 "${SCRIPT_DIR}/systemd/rflens-aprs.service" "${UNIT_DIR}/rflens-aprs.service"

sudo systemctl daemon-reload
sudo systemctl enable --now rflens-api

cat <<'EOF'
RFLens systemd service templates installed.

Enabled now:
  rflens-api

Optional sources:
  sudo systemctl enable --now rflens-aprs
  sudo systemctl enable --now rflens-adsb
  sudo systemctl enable --now rflens-aprs-radio

Enable only the sources configured for this station.
EOF
