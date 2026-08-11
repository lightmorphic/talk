#!/bin/bash
# Talkin launcher. Offline/online is decided in talkin/engine.py: the
# very first run is allowed to download the speech model once, then
# every run after that is pinned hard-offline. Nothing is forced here.
cd "$(dirname "$0")/.." || exit 1
exec .venv/bin/python -m talkin "$@"
