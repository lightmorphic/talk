#!/usr/bin/env bash
# Run Talkmorphic with full logging into ./debug/ so a fault can be diagnosed.
# Settings, history and the speech model are untouched — only the log moves.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$HERE/debug"
export TALKMORPHIC_LOG_DIR="$HERE/debug"
echo "Logging to $HERE/debug/"
echo "Use Talkmorphic as normal. When it misbehaves, press Ctrl+C here."
exec "$HERE/Talkmorphic-x86_64.AppImage" 2>&1 | tee "$HERE/debug/console.log"
