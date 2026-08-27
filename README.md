# Talkin

Private, on-device dictation for Linux. Click, speak, click again. Your
words are typed into whatever app you're using, and your voice never
leaves your machine.

Works on Wayland and on X11 from the same AppImage. On Wayland it types
through the desktop's RemoteDesktop portal, which needs GNOME 45+ or KDE
Plasma 5.27+ and is approved once on first run. On X11 it types directly
and works on any desktop — Cinnamon, XFCE, MATE, GNOME, KDE, or a bare
window manager — with nothing to approve.

## How it works

- Click the floating button, or the tray icon, and speak. Click again to
  stop. The tray icon shows a live waveform while
  Talkin hears you, and a revolving spinner while it thinks. Release
  (or click again), and the text appears where your cursor is.
- Speech recognition runs locally on your CPU using NVIDIA's
  [Parakeet TDT 0.6b v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
  model (CC-BY-4.0) via [onnx-asr](https://github.com/istupakov/onnx-asr),
  with no cloud, no accounts, and no audio ever sent anywhere.
- A cleanup pass removes filler words (um, uh, etc.) and applies your
  personal dictionary.
- Teach it words: click the small button beside the record button, type
  the word it got wrong and the right spelling once. Never see the mistake
  again. Your dictionary can be exported and imported as a file.
- Every visible string lives in `locales/translations.csv`, one
  human-editable file. Add a column, get a new language.

## Install

Download the latest `Talkin-x86_64.AppImage` from
[Releases](https://github.com/lightmorphic/talkin/releases/latest),
make it executable, and run it, and that's the whole install:

```bash
chmod +x Talkin-x86_64.AppImage
./Talkin-x86_64.AppImage
```

The speech model (~600 MB) downloads once on first run, then the app
is pinned hard-offline. Nothing else to install.

**Requirements: a Wayland session on GNOME 45+ or KDE Plasma 5.27+
(needs the RemoteDesktop portal), plus PipeWire or
PulseAudio.** See the notice at the top of this file.

## Building from source

```bash
git clone https://github.com/lightmorphic/talkin
cd talkin
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
./scripts/talkin.sh
```

Needs Python 3.11+ and GTK 3 with AppIndicator
(`gir1.2-ayatanaappindicator3-0.1`) on the system. The first run
downloads the speech model (~600 MB) from Hugging Face, then the app
is pinned hard-offline.

## Settings

Right-click the tray icon → Settings, a native window with
microphone, cleanup, dictionary, history, translations, updates and
maintenance.

## Privacy

Zero telemetry, zero analytics, zero network traffic at runtime: the
launcher pins the process offline. The only network access ever is the
one-time model download at install, and the update check, which runs
only when you open Settings or click the update dot, and talks only to
GitHub.

## Licence

GPL-3.0-or-later. The Parakeet model is CC-BY-4.0 (© NVIDIA).

Created by [Lightmorphic](https://lightmorphic.com).
