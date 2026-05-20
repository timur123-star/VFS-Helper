#!/usr/bin/env bash
# ============================================================
#  VFS Helper - one-line setup for macOS / Linux
# ============================================================
#  Usage:  bash setup.sh
# ============================================================
set -e

cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo
    echo "python3 was not found. Install Python 3.10+ first."
    echo "  macOS:  brew install python"
    echo "  Linux:  sudo apt install python3 python3-venv  (or your distro equivalent)"
    exit 1
fi

if [ ! -d .venv ]; then
    echo "Creating virtual environment in .venv ..."
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
. .venv/bin/activate

echo
echo "Installing Python dependencies ..."
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt

echo
echo "Installing Playwright Chromium (fallback browser) ..."
python -m playwright install chromium

echo
echo "Running diagnostics ..."
python vfs_helper.py --check || true

echo
echo "============================================================"
echo " Setup finished."
echo " Next step: enter your data via the wizard."
echo "============================================================"
echo
python vfs_helper.py --setup

echo
echo "Done. To start the helper later run:  ./run.sh"
