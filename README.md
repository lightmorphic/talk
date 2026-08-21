# Talkin

> ## ⚠️ Works on X11 only — on Wayland, use [Twalkin](https://twalkin.lightmorphic.co.uk)
>
> **Talkin requires an X11 (Xorg) desktop session. It will not work on a
> Wayland session.** For Wayland there is a sister project,
> **[Twalkin](https://twalkin.lightmorphic.co.uk)**, built for Wayland
> from the ground up.
>
> Talkin listens for a global hotkey and types text into whatever app you
> are using. Wayland does not permit either of those things the way X11
> does, so on a Wayland session the app will start but the hotkey will
> never fire and no text will be typed. That difference runs deep enough
> that each display server gets its own app, rather than one app that
> half-works on both.
>
> **How to check which one you are using** — run this in a terminal:
>
> ```bash
> echo $XDG_SESSION_TYPE
> ```
>
> `x11` → Talkin (this project).
> `wayland` → [Twalkin](https://twalkin.lightmorphic.co.uk).
>
> Many desktops (KDE Plasma, Cinnamon, Xfce, MATE) still offer an X11
> session on the login screen. Note that **GNOME 45+ defaults to Wayland,
> and GNOME 50 removed the X11 session entirely**, so on recent GNOME you
> want Twalkin.

Private, on-device dictation for the Linux desktop. Hold a key, speak,
let go. Your words are typed into whatever app you're using, and your
voice never leaves your machine.

**Website:** [talkin.lightmorphic.co.uk](https://talkin.lightmorphic.co.uk)

## How it works

- Hold **Right Ctrl** (configurable) and speak, or left-click the tray
  icon to start and stop. The tray icon shows a live waveform while
  Talkin hears you, and a revolving spinner while it thinks. Release
  (or click again), and the text appears where your cursor is.
- Speech recognition runs locally on your CPU using NVIDIA's
  [Parakeet TDT 0.6b v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
  model (CC-BY-4.0) via [onnx-asr](https://github.com/istupakov/onnx-asr),
  with no cloud, no accounts, and no audio ever sent anywhere.
- A cleanup pass removes filler words (um, uh, etc.) and applies your
  personal dictionary.
- Teach it words: highlight any word Talkin mistyped, press
  **Ctrl+Alt+C**, type the right spelling once. Never see the mistake
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

**Requirements: Linux running an X11 (Xorg) session — not Wayland — plus
PipeWire or PulseAudio.** See the notice at the top of this file.

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

Right-click the tray icon → Settings, a native window with hotkeys,
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

Created by [Lightmorphic](https://lightmorphic.co.uk).
