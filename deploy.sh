#!/usr/bin/env bash
set -euo pipefail

echo "==> Pulling latest RF Lens code"
git pull

echo "==> Activating virtualenv"
source venv/bin/activate

echo "==> Installing Python dependencies"
pip install -r requirements.txt

echo "==> Initializing database"
python -m scripts.init_db

echo "==> Restarting RF Lens API service"
sudo systemctl restart rflens-api

echo "==> Restarting RF Lens ADS-B service"
sudo systemctl restart rflens-adsb

echo "==> RF Lens API status"
sudo systemctl status rflens-api --no-pager

echo "==> RF Lens ADS-B status"
sudo systemctl status rflens-adsb --no-pager
