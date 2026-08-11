"""Global hotkeys via one pynput listener.

Three independent combos, each fully user-chosen (not picked from a
preset list): hold-to-talk, toggle-dictation and correction. Any of
them can be any modifier(s)+key combination, or left unset entirely.
A combo is represented as a canonical string like "alt+z", "ctrl+alt+c"
or a bare special key like "f9" — see parse_combo(). Callbacks are
marshalled onto the GTK main loop.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging

from gi.repository import GLib
from pynput import keyboard

log = logging.getLogger("talkin.hotkeys")

MODIFIER_NAMES = ("ctrl", "alt", "shift")

_MODIFIER_KEYS = {
    "ctrl": {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r},
    "alt": {keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r,
            keyboard.Key.alt_gr},
    "shift": {keyboard.Key.shift, keyboard.Key.shift_l,
              keyboard.Key.shift_r},
}

# Keys that don't type anything by themselves, so they're safe to use
# as a hotkey trigger with no modifier at all.
NON_PRINTING_KEYS = {
    "ctrl_r", "alt_r", "shift_r", "f1", "f2", "f3", "f4", "f5", "f6",
    "f7", "f8", "f9", "f10", "f11", "f12", "pause", "scroll_lock",
    "menu", "insert", "delete", "home", "end", "page_up", "page_down",
    "up", "down", "left", "right", "tab", "escape",
}


def _specific_token(key):
    """The exact key pressed — 'ctrl_r', 'f9', 'z', etc — usable as a
    trigger even when that exact key is itself a modifier (a combo like
    bare "ctrl_r" needs to tell right-Ctrl apart from left-Ctrl)."""
    char = getattr(key, "char", None)
    if char is not None:
        if len(char) == 1 and ord(char) < 27:
            # With Ctrl held, X11 reports letters as control codes.
            char = chr(ord(char) + 96)
        return char.lower()
    name = getattr(key, "name", None)
    return name.lower() if name else None


def _modifier_category(key):
    """Which of ctrl/alt/shift this key counts as, for combos that
    require 'any variant of this modifier' rather than a specific side."""
    for name, variants in _MODIFIER_KEYS.items():
        if key in variants:
            return name
    return None


def parse_combo(text):
    """"alt+z" -> (frozenset({"alt"}), "z"). ("", None) if unset/invalid."""
    text = (text or "").strip().lower()
    if not text:
        return frozenset(), None
    parts = [p for p in text.split("+") if p]
    if not parts:
        return frozenset(), None
    trigger = parts[-1]
    mods = frozenset(p for p in parts[:-1] if p in MODIFIER_NAMES)
    return mods, trigger


def combo_is_safe(text):
    """A combo with a printable trigger must carry at least one modifier,
    or every ordinary keystroke anywhere would fire it."""
    mods, trigger = parse_combo(text)
    if trigger is None:
        return True  # unset is always fine
    if trigger in NON_PRINTING_KEYS:
        return True
    return len(mods) > 0


class _ComboWatch:
    """Tracks the press/unsatisfied edge of one combo against live state."""

    def __init__(self, combo_text):
        self.mods, self.trigger = parse_combo(combo_text)
        self.active = False

    @property
    def enabled(self):
        return self.trigger is not None

    def update(self, down_mods, down_others):
        was_active = self.active
        self.active = self.enabled and self.mods <= down_mods \
            and self.trigger in down_others
        if self.active and not was_active:
            return "pressed"
        if was_active and not self.active:
            return "released"
        return None


class Hotkeys:
    """Fires on_hold_press/on_hold_release while the hold combo is held,
    on_toggle each time the toggle combo is pressed, on_correction each
    time the correction combo is pressed — all on the GTK main loop."""

    def __init__(self, config, on_hold_press, on_hold_release,
                on_toggle, on_correction):
        self.config = config
        self.on_hold_press = on_hold_press
        self.on_hold_release = on_hold_release
        self.on_toggle = on_toggle
        self.on_correction = on_correction
        self._down_mods = set()
        self._down_others = set()
        self._watches = {}
        self.reload()
        self._listener = keyboard.Listener(
            on_press=self._pressed, on_release=self._released)
        self._listener.daemon = True
        self._listener.start()

    def reload(self):
        """Re-read combos from config (call after Settings changes)."""
        self._watches = {
            "hold": _ComboWatch(self.config.get("hotkey_hold")),
            "toggle": _ComboWatch(self.config.get("hotkey_toggle")),
            "correction": _ComboWatch(self.config.get("correction_hotkey")),
        }

    def _pressed(self, key):
        specific = _specific_token(key)
        if specific is None:
            return
        self._down_others.add(specific)
        mod = _modifier_category(key)
        if mod:
            self._down_mods.add(mod)
        self._dispatch()

    def _released(self, key):
        specific = _specific_token(key)
        if specific is None:
            return
        self._down_others.discard(specific)
        mod = _modifier_category(key)
        if mod:
            self._down_mods.discard(mod)
        self._dispatch()

    def _dispatch(self):
        edge = self._watches["hold"].update(self._down_mods, self._down_others)
        if edge == "pressed":
            GLib.idle_add(self.on_hold_press)
        elif edge == "released":
            GLib.idle_add(self.on_hold_release)

        if self._watches["toggle"].update(
                self._down_mods, self._down_others) == "pressed":
            GLib.idle_add(self.on_toggle)

        if self._watches["correction"].update(
                self._down_mods, self._down_others) == "pressed":
            GLib.idle_add(self.on_correction)

    def stop(self):
        self._listener.stop()
