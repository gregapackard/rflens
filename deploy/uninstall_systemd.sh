#!/usr/bin/env bash
set -euo pipefail

UNIT_DIR="/etc/systemd/system"

sudo systemctl disable --now rflens-api rflens-adsb rflens-aprs rflens-aprs-radio 2>/dev/null || true
sudo rm -f \
  "${UNIT_DIR}/rflens-api.service" \
  "${UNIT_DIR}/rflens-adsb.service" \
  "${UNIT_DIR}/rflens-aprs-radio.service" \
  "${UNIT_DIR}/rflens-aprs.service"
sudo systemctl daemon-reload
sudo systemctl reset-failed rflens-api rflens-adsb rflens-aprs rflens-aprs-radio 2>/dev/null || true

echo "RF Lens systemd services removed."
