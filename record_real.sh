#!/usr/bin/env bash
# ============================================================
#  VFS Helper - record a FULL autofill demo on the REAL VFS form
#  macOS / Linux launcher. See scripts/record_real_runner.py.
#  Sequence: inspect -> scroll -> fill (x2) -> fill applicant -> screenshot
# ============================================================
set -e

cd "$(dirname "$0")"

if [ ! -f .venv/bin/activate ]; then
    echo
    echo "[ERROR] .venv not found. Please run ./setup.sh first."
    exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo
    echo "[ERROR] ffmpeg is not in PATH. Install it:"
    echo "  macOS: brew install ffmpeg"
    echo "  Linux: sudo apt install ffmpeg"
    exit 1
fi

# shellcheck disable=SC1091
. .venv/bin/activate

if [ ! -f config.json ]; then
    if [ -f config.demo-full.json ]; then
        echo
        echo "[INFO] Copying config.demo-full.json -> config.json for the recording."
        echo "       Edit config.json with your real data before recording."
        cp config.demo-full.json config.json
    else
        echo
        echo "[INFO] config.json not found. Launching setup wizard..."
        python vfs_helper.py --setup
    fi
fi

python scripts/record_real_runner.py --platform linux "$@"
