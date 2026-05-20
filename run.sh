#!/usr/bin/env bash
# ============================================================
#  VFS Helper - launcher for macOS / Linux
# ============================================================
set -e

cd "$(dirname "$0")"

if [ ! -f .venv/bin/activate ]; then
    echo
    echo "Virtual environment not found. Please run ./setup.sh first."
    exit 1
fi

# shellcheck disable=SC1091
. .venv/bin/activate

if [ ! -f config.json ]; then
    echo
    echo "config.json not found. Launching setup wizard ..."
    echo
    python vfs_helper.py --setup
fi

python vfs_helper.py "$@"
