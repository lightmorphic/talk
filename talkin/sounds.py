"""Two very short tones: one when dictation starts, one when it stops.

Without them there is nothing to tell you the microphone is open. The
floating button changes, but nobody is looking at the button while
dictating — they are looking at what they are dictating into, so the one
cue that actually reaches you has to be a sound.

They are deliberately quiet and brief. This plays several times an hour
all day, under whatever else is making noise; anything longer or louder
than a soft two-note blip becomes a thing to switch off.

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

log = logging.getLogger("talkin.sounds")

RATE = 44100
_NOTE_S = 0.055        # each of the two notes
_GAIN = 0.16           # quiet: this is a cue, not an alert
_FADE_S = 0.008        # ramp in and out, or the speaker clicks

# Rising to start, falling to stop, so the two are told apart without
# being looked at. A fifth apart: distinct, and pleasant repeated.
NOTES = {
    "start": (660.0, 990.0),
    "stop": (990.0, 660.0),
}

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


def _samples(frequencies):
    """A sine blip per frequency, joined, with the edges ramped."""
    out = []
    fade = int(RATE * _FADE_S)
    for freq in frequencies:
        n = int(RATE * _NOTE_S)
        for i in range(n):
            value = math.sin(2 * math.pi * freq * i / RATE) * _GAIN
            if fade and n > 2 * fade:
                if i < fade:
                    value *= i / fade
                elif i >= n - fade:
                    value *= (n - i) / fade
            out.append(value)
    return out


def _wav_path(name):
    """A 16-bit mono WAV of the tone, written once per run."""
    with _lock:
        path = _files.get(name)
        if path and os.path.exists(path):
            return path
        handle, path = tempfile.mkstemp(prefix="talkin-" + name + "-",
                                        suffix=".wav")
        os.close(handle)
        with wave.open(path, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(RATE)
            out.writeframes(b"".join(
                struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767))
                for v in _samples(NOTES[name])))
        _files[name] = path
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


def _play_via_portaudio(name):
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
    sd.play(np.array(_samples(NOTES[name]), dtype=np.float32), RATE,
            device=device, blocking=True)
    return "portaudio"


def play(name):
    """Play "start" or "stop". Returns at once; never raises."""
    def worker():
        try:
            route = _route[0]
            if route != "portaudio":
                route = _play_via_command(_wav_path(name))
            if route is None:
                route = _play_via_portaudio(name)
            if _route[0] != route:
                _route[0] = route
                log.info("playing cue sounds through %s", route)
        except Exception as exc:
            # Debug, not warning: a machine with no working output is a
            # normal thing to be, and a warning would fill the log twice
            # per dictation for the rest of its life.
            log.debug("could not play the %s sound: %s", name, exc)

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
