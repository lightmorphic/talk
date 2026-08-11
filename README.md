# Talkin

Private, on-device dictation for the Linux desktop. Hold a key, speak,
let go — your words are typed into whatever app you're using. Your
voice never leaves your machine.

**Website:** [talkin.lightmorphic.co.uk](https://talkin.lightmorphic.co.uk)

## How it works

- Hold **Right Ctrl** (configurable) and speak. A small circle shows a
  live waveform while Talkin hears you, and a revolving spinner while
  it thinks. Release, and the text appears where your cursor is.
- Speech recognition runs locally on your CPU using NVIDIA's
  [Parakeet TDT 0.6b v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
  model (CC-BY-4.0) via [onnx-asr](https://github.com/istupakov/onnx-asr) —
  no cloud, no accounts, no audio ever sent anywhere.
- A cleanup pass removes filler words (um, uh…) and applies your
  personal dictionary.
- Teach it words: highlight any word Talkin mistyped, press
  **Ctrl+Alt+C**, type the right spelling once. Never see the mistake
  again. Your dictionary can be exported and imported as a file.
- Every visible string lives in `locales/translations.csv` — one
  human-editable file. Add a column, get a new language.

## Install

```bash
git clone https://github.com/lightmorphic/talkin
cd talkin
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
./scripts/talkin.sh
```

Requirements: Linux with X11, PipeWire/PulseAudio, Python 3.11+, GTK 3
with AppIndicator (`gir1.2-ayatanaappindicator3-0.1`). The first run
downloads the speech model (~600 MB) from Hugging Face; after that the
app is pinned hard-offline.

## Settings

Right-click the tray icon → Settings. Hotkeys, microphone, cleanup,
dictionary, history, translations, updates and maintenance — all in
the browser, all local (127.0.0.1 only).

## Privacy

Zero telemetry. Zero analytics. Zero network traffic at runtime — the
launcher pins the process offline. The only network access ever is the
one-time model download at install, and the update check, which runs
only when you open Settings or click the update dot, and talks only to
GitHub.

## Licence

MIT. The Parakeet model is CC-BY-4.0 (© NVIDIA).

Created by [Lightmorphic](https://lightmorphic.co.uk).
