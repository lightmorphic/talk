#!/bin/bash
# Talkin launcher. Pins the app hard-offline: the model is on disk, so
# nothing ever needs (or gets) network access.
cd "$(dirname "$0")/.." || exit 1
export HF_HUB_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1
export HF_HOME="$PWD/models/hf-cache"
exec .venv/bin/python -m talkin "$@"
