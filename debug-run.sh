#!/usr/bin/env bash
# Run Talkin with full logging into ./debug/ so a fault can be diagnosed.
# Settings, history and the speech model are untouched — only the log moves.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$HERE/debug"
export TALKIN_LOG_DIR="$HERE/debug"
echo "Logging to $HERE/debug/"
echo "Use Talkin as normal. When it misbehaves, press Ctrl+C here."
exec "$HERE/Talkin-x86_64.AppImage" 2>&1 | tee "$HERE/debug/console.log"
