#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

VENV_DIR="${RFLENS_VENV:-venv}"

if [ ! -d "$VENV_DIR" ]; then
  echo "==> Creating Python virtual environment in $VENV_DIR"
  python3 -m venv "$VENV_DIR"
else
  echo "==> Using existing Python virtual environment in $VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "==> Installing Python dependencies"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [ ! -f config.yaml ]; then
  echo "==> Creating config.yaml from config.example.yaml"
  cp config.example.yaml config.yaml
else
  echo "==> Keeping existing config.yaml"
fi

mkdir -p data/logs

echo "==> Initializing SQLite database"
python -m scripts.init_db

echo
echo "RFLens setup complete."
echo
echo "Next steps:"
echo "  1. Edit config.yaml for your station and enable only the sources you have."
echo "  2. Run: python scripts/check_setup.py"
echo "  3. Start the API: bash ./scripts/run_api.sh"
echo "  4. Open: http://localhost:8080/ui"
