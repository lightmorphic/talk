"""Global hotkeys via one pynput listener.

The dictation key is a single named key (push-to-talk needs clean
press/release semantics). The correction hotkey is a modifier combo
like ctrl+alt+c. Callbacks are marshalled onto the GTK main loop.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging

from gi.repository import GLib
from pynput import keyboard

log = logging.getLogger("talkin.hotkeys")

# Keys offered in Settings for dictation. Right-side modifiers and F-keys
# don't type anything by themselves, which makes them safe to hold.
DICTATION_KEYS = [
    "ctrl_r", "alt_r", "shift_r", "f1", "f2", "f3", "f4", "f5", "f6",
    "f7", "f8", "f9", "f10", "f12", "pause", "scroll_lock", "menu",
]

CORRECTION_KEYS = [
    "ctrl+alt+c", "ctrl+alt+w", "ctrl+alt+t", "f7", "f8", "f9",
]

_MODIFIERS = {
    "ctrl": {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r},
    "alt": {keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r,
            keyboard.Key.alt_gr},
    "shift": {keyboard.Key.shift, keyboard.Key.shift_l,
              keyboard.Key.shift_r},
}


def _named_key(name):
    try:
        return getattr(keyboard.Key, name)
    except AttributeError:
        return keyboard.KeyCode.from_char(name)


class Hotkeys:
    """on_press_key/on_release_key fire for the dictation key,
    on_correction for the correction combo — all on the GTK loop."""

    def __init__(self, config, on_press_key, on_release_key, on_correction):
        self.config = config
        self.on_press_key = on_press_key
        self.on_release_key = on_release_key
        self.on_correction = on_correction
        self._down_modifiers = set()
        self._dictation_down = False
        self._listener = keyboard.Listener(
            on_press=self._pressed, on_release=self._released)
        self._listener.daemon = True
        self._listener.start()

    def _dictation_key(self):
        return _named_key(self.config.get("hotkey"))

    def _correction_parts(self):
        parts = str(self.config.get("correction_hotkey")).split("+")
        mods = frozenset(p for p in parts[:-1] if p in _MODIFIERS)
        return mods, _named_key(parts[-1])

    def _canonical(self, key):
        for name, keys in _MODIFIERS.items():
            if key in keys:
                return name
        return None

    def _pressed(self, key):
        mod = self._canonical(key)
        if mod:
            self._down_modifiers.add(mod)

        if key == self._dictation_key():
            if not self._dictation_down:
                self._dictation_down = True
                GLib.idle_add(self.on_press_key)
            return

        mods, trigger = self._correction_parts()
        pressed_char = getattr(key, "char", None)
        trigger_char = getattr(trigger, "char", None)
        if pressed_char and len(pressed_char) == 1 and ord(pressed_char) < 27:
            # With Ctrl held, X11 reports letters as control codes.
            pressed_char = chr(ord(pressed_char) + 96)
        char_match = (pressed_char is not None and trigger_char is not None
                      and pressed_char.lower() == trigger_char.lower())
        if (key == trigger or char_match) and mods <= self._down_modifiers:
            if mods or trigger_char is None:  # bare letter never triggers
                GLib.idle_add(self.on_correction)

    def _released(self, key):
        mod = self._canonical(key)
        if mod:
            self._down_modifiers.discard(mod)
        if key == self._dictation_key() and self._dictation_down:
            self._dictation_down = False
            GLib.idle_add(self.on_release_key)

    def stop(self):
        self._listener.stop()
