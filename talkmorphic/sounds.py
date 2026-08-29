"""Ten short tone sets: one plays when dictation starts, another when it
stops. The choice lives in Settings.

Without them there is nothing to tell you the microphone is open. The
floating button changes, but nobody is looking at the button while
dictating — they are looking at what they are dictating into, so the one
cue that actually reaches you has to be a sound.

They are deliberately quiet and brief. This plays several times an hour
all day, under whatever else is making noise; anything longer or louder
than a soft blip becomes a thing to switch off, which is also why there
is more than one to choose from rather than one true design.

Every theme comes out of the same small synthesiser rather than ten
hand-written ones — a shape (how the volume rises and falls across the
note), a pair of frequencies (start, and stop as its mirror), and a
duration are enough to make a chime, a click, a bell and a sweep all
sound like clearly different things.

Getting it to the speakers is the awkward part. PortAudio is already
bundled for the microphone, but the bundled build speaks only ALSA, so
its idea of the default output is the first sound card — routinely an
HDMI socket with nothing plugged into it — while the desktop's real
output is a PipeWire or PulseAudio sink it cannot see. Playing through it
is therefore the last resort, not the first, and even then a card with
"HDMI" in its name is passed over. Everything before it hands a small WAV
to whatever the desktop itself uses, which is the only way to land on the
output the user is actually listening to.

Nothing here is allowed to matter. A failure to beep must never disturb a
dictation, so every path swallows its errors and carries on.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import math
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import wave

log = logging.getLogger("talkmorphic.sounds")

RATE = 44100
_GAIN = 0.08           # quiet: this is a cue, not an alert

# id -> (native name, shape, (start freq(s)), duration seconds, 2nd-
# harmonic strength, whether the frequencies glide continuously rather
# than being separate notes, gap between repeated notes in seconds).
# `stop` is always the same shape played with the frequency list
# reversed, which is what makes the pair read as a matched "on/off"
# rather than two unrelated noises.
#
# Chosen a fourth apart at most and never above ~1.2kHz: the original
# single design sat right where the ear is most sensitive, with a sharp
# linear edge, and read as a poke rather than a chime however briefly it
# played. Nothing here repeats that.
THEMES = {
    "chime":     ("Chime",     "hann", (440, 550),   0.075, 0.0,  False, 0),
    "click":     ("Click",     "tri",  (700,),        0.02,  0.0,  False, 0),
    "bell":      ("Bell",      "exp",  (660,),        0.35,  0.35, False, 0),
    "marimba":   ("Marimba",   "exp",  (330, 415),    0.09,  0.25, False, 0),
    "bubble":    ("Bubble",    "hann", (350, 700),    0.14,  0.0,  True,  0),
    "sweep":     ("Sweep",     "hann", (300, 900),    0.22,  0.0,  True,  0),
    "pulse":     ("Pulse",     "tri",  (600, 600),    0.035, 0.0,  False, 0.03),
    "xylophone": ("Xylophone", "exp",  (880, 1046),   0.06,  0.3,  False, 0),
    "drop":      ("Drop",      "hann", (500, 900),    0.12,  0.15, True,  0),
    "ping":      ("Ping",      "exp",  (1200,),       0.05,  0.0,  False, 0),
}
DEFAULT_THEME = "chime"

# Desktop players, best first. pw-play and paplay go through the sound
# server, so they land on whatever the user has selected as their output
# and respect the system volume; aplay is the bare-ALSA last of these.
_PLAYERS = (
    ("pw-play", ()),
    ("paplay", ()),
    ("canberra-gtk-play", ("-f",)),
    ("aplay", ("-q",)),
)

_lock = threading.Lock()
_files = {}
_route = [None]        # remembered once, so the search happens once


def theme_names():
    """(id, native name) for every theme, in a fixed, sensible order."""
    return [(theme_id, THEMES[theme_id][0]) for theme_id in THEMES]


def _envelope(shape, i, n):
    if shape == "hann":
        # One smooth rise and fall across the WHOLE note. Not a short
        # fade at each edge with a flat, buzzy plateau in between — that
        # plateau plus a fast linear ramp is exactly what reads as a
        # click or a poke. This removes the plateau entirely, so there
        # is never a moment of full volume to be sharp about.
        return 0.5 - 0.5 * math.cos(2 * math.pi * i / max(1, n - 1))
    if shape == "tri":
        half = n / 2
        return i / half if i < half else (n - i) / half
    if shape == "exp":
        # A fast attack and a longer decay - a struck, resonant sound
        # (bell, marimba) rather than a breathed one.
        return math.exp(-5.0 * i / n)
    return 1.0


def _tone(shape, freqs, note_s, harmonic, glide, gap_s):
    """The sample list for one direction (start, or stop as its mirror)."""
    n = int(RATE * note_s)
    gap = [0.0] * int(RATE * gap_s) if gap_s else []
    out = []
    if glide:
        f0, f1 = freqs[0], freqs[-1]
        phase = 0.0
        for i in range(n):
            freq = f0 + (f1 - f0) * (i / max(1, n - 1))
            phase += 2 * math.pi * freq / RATE
            env = _envelope("hann", i, n)
            value = math.sin(phase) * _GAIN * env
            if harmonic:
                value += math.sin(2 * phase) * _GAIN * harmonic * env
            out.append(value)
        return out
    for index, freq in enumerate(freqs):
        for i in range(n):
            env = _envelope(shape, i, n)
            value = math.sin(2 * math.pi * freq * i / RATE) * _GAIN * env
            if harmonic:
                value += (math.sin(2 * math.pi * freq * 2 * i / RATE)
                          * _GAIN * harmonic * env)
            out.append(value)
        if index < len(freqs) - 1:
            out.extend(gap)
    return out


def _samples(theme_id, which):
    _name, shape, freqs, note_s, harmonic, glide, gap_s = THEMES.get(
        theme_id, THEMES[DEFAULT_THEME])
    if which == "stop":
        freqs = tuple(reversed(freqs))
    return _tone(shape, freqs, note_s, harmonic, glide, gap_s)


def _wav_path(theme_id, which):
    """A 16-bit mono WAV of the tone, written once per run."""
    key = (theme_id, which)
    with _lock:
        path = _files.get(key)
        if path and os.path.exists(path):
            return path
        handle, path = tempfile.mkstemp(
            prefix="talkmorphic-{}-{}-".format(theme_id, which), suffix=".wav")
        os.close(handle)
        with wave.open(path, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(RATE)
            out.writeframes(b"".join(
                struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767))
                for v in _samples(theme_id, which)))
        _files[key] = path
        return path


def _play_via_command(path):
    for command, flags in _PLAYERS:
        binary = shutil.which(command)
        if binary is None:
            continue
        try:
            subprocess.run([binary] + list(flags) + [path],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,
                           timeout=5, check=True)
            return command
        except Exception as exc:
            log.debug("%s could not play the tone: %s", command, exc)
    return None


def _play_via_portaudio(theme_id, which):
    """Last resort, skipping HDMI outputs nothing is plugged into."""
    import numpy as np
    import sounddevice as sd

    device = None
    try:
        for index, info in enumerate(sd.query_devices()):
            if info["max_output_channels"] < 1:
                continue
            if "hdmi" in info["name"].lower():
                continue
            device = index
            break
    except Exception:
        pass
    sd.play(np.array(_samples(theme_id, which), dtype=np.float32), RATE,
            device=device, blocking=True)
    return "portaudio"


def play(which, theme_id=DEFAULT_THEME):
    """Play "start" or "stop" in the given theme. Never raises."""
    def worker():
        try:
            route = _route[0]
            if route != "portaudio":
                route = _play_via_command(_wav_path(theme_id, which))
            if route is None:
                route = _play_via_portaudio(theme_id, which)
            if _route[0] != route:
                _route[0] = route
                log.info("playing cue sounds through %s", route)
        except Exception as exc:
            # Debug, not warning: a machine with no working output is a
            # normal thing to be, and a warning would fill the log twice
            # per dictation for the rest of its life.
            log.debug("could not play the %s/%s sound: %s",
                      theme_id, which, exc)

    threading.Thread(target=worker, daemon=True).start()


def cleanup():
    """Remove the temporary WAVs. Called when quitting."""
    with _lock:
        for path in _files.values():
            try:
                os.remove(path)
            except OSError:
                pass
        _files.clear()
