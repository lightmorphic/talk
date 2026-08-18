"""Global hotkeys via one pynput listener plus X server key grabs.

Three independent combos, each fully user-chosen (not picked from a
preset list): hold-to-talk, toggle-dictation and correction. Any of
them can be any modifier(s)+key combination, or left unset entirely.
A combo is represented as a canonical string like "alt+z", "ctrl+alt+c"
or a bare special key like "f9" — see parse_combo(). Callbacks are
marshalled onto the GTK main loop.

The pynput listener only OBSERVES the global key stream (XRecord); it
cannot stop a keystroke from also being delivered to whatever window
has focus. For a combo like alt+z that means pressing it both started
dictation AND handed alt+z to the focused app — which, depending on the
app, inserted a literal "z" right where the transcript was about to go
(others treat it as an unknown shortcut and swallow it, which is why
the stray character only appeared sometimes). So any combo whose
trigger is a printable character is ALSO grabbed at the X server level
(XGrabKey): the server then delivers it to Talkin alone and the focused
app never sees it. XRecord taps the stream regardless of grabs, so the
pynput state machine below keeps working unchanged — both verified
directly against a live GTK entry before this shipped.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import queue
import threading

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


class _ComboGrabber:
    """Consumes printable-trigger combos at the X server so the focused
    app never receives them (see the module docstring for why).

    One dedicated X connection, touched only by its own daemon thread —
    python-xlib is not thread-safe, so grab/ungrab requests from the
    GTK side arrive through a queue and the thread applies them. Grabs
    cover the NumLock/CapsLock mask variants, since X treats those as
    part of the modifier state. If there is no X display to talk to
    (Wayland session, headless), this quietly does nothing and hotkeys
    simply keep their old observe-only behaviour.
    """

    def __init__(self):
        self._commands = queue.Queue()
        self._grabbed = []  # [(keycode, mask)] currently held grabs
        threading.Thread(target=self._run, name="combo-grabber",
                         daemon=True).start()

    def set_combos(self, combos):
        """combos: [(frozenset of modifier names, single-char trigger)]"""
        self._commands.put(list(combos))

    def _run(self):
        try:
            from Xlib import X, XK
            from Xlib import display as xdisplay
            from Xlib import error as xerror
            disp = xdisplay.Display()
        except Exception:
            log.info("no X display for key grabs; combos stay observe-only")
            return
        root = disp.screen().root
        mask_for = {"ctrl": X.ControlMask, "alt": X.Mod1Mask,
                    "shift": X.ShiftMask}
        lock_variants = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)

        def apply(combos):
            catch = xerror.CatchError()
            for keycode, mask in self._grabbed:
                root.ungrab_key(keycode, mask, onerror=catch)
            self._grabbed = []
            for mods, trigger in combos:
                # Latin-1 keysyms equal their code point, which covers
                # every printable trigger _specific_token() can produce.
                keysym = XK.string_to_keysym(trigger) or ord(trigger)
                keycode = disp.keysym_to_keycode(keysym)
                if not keycode:
                    log.warning("no keycode for %r; cannot grab", trigger)
                    continue
                base = 0
                for name in mods:
                    base |= mask_for.get(name, 0)
                for extra in lock_variants:
                    root.grab_key(keycode, base | extra, 0,
                                  X.GrabModeAsync, X.GrabModeAsync,
                                  onerror=catch)
                    self._grabbed.append((keycode, base | extra))
                log.info("grabbed %s+%s at the X server",
                         "+".join(sorted(mods)) or "(none)", trigger)
            disp.sync()
            if catch.get_error():
                # Most likely another client already owns one of these
                # combos - the hotkey still works (pynput observes it),
                # it just can't be exclusively consumed.
                log.warning("some key grabs failed: %s", catch.get_error())

        while True:
            # Delivered grab events only need draining; pynput is the
            # one actually acting on key state.
            try:
                while disp.pending_events():
                    disp.next_event()
            except Exception:
                log.exception("grab connection lost; combos observe-only")
                return
            try:
                apply(self._commands.get(timeout=0.05))
            except queue.Empty:
                pass


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
        self._pending_release = {}
        self._debounce_lock = threading.Lock()
        self._grabber = _ComboGrabber()
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
        # Only printable triggers leak text into the focused app; bare
        # non-printing keys (ctrl_r, f9...) type nothing and stay
        # ungrabbed so they keep working even where a grab would fail.
        self._grabber.set_combos([
            (watch.mods, watch.trigger)
            for watch in self._watches.values()
            if watch.enabled and watch.trigger not in NON_PRINTING_KEYS
            and len(watch.trigger) == 1])

    # X keyboard auto-repeat delivers a RELEASE+press pair (~1ms apart,
    # ~30x/second) for every held non-modifier key. Taken at face value
    # each pair made a held combo like alt+z flap released/pressed for
    # as long as the key was down - hold-to-talk stopped the recording
    # on every spurious release, transcribed the fragment, restarted on
    # the re-press, and everything said in the gaps was lost ("it shows
    # listening but only half of what I said appears"). The old default
    # hold key never showed it because modifiers don't auto-repeat. So
    # a release only counts if the same key isn't pressed again within
    # this window: repeat pairs arrive ~1ms apart, real finger lifts
    # never re-press this fast. Costs that many ms of latency on the
    # real release - imperceptible next to ~300ms of transcription.
    _RELEASE_DEBOUNCE_S = 0.04

    def _pressed(self, key):
        specific = _specific_token(key)
        if specific is None:
            return
        with self._debounce_lock:
            timer = self._pending_release.pop(specific, None)
            if timer is not None:
                # Auto-repeat pair: swallow the release AND this press -
                # the key never really left the down state.
                timer.cancel()
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
        with self._debounce_lock:
            old = self._pending_release.pop(specific, None)
            if old is not None:
                old.cancel()
            timer = threading.Timer(
                self._RELEASE_DEBOUNCE_S, self._commit_release, [key, specific])
            timer.daemon = True
            self._pending_release[specific] = timer
            timer.start()

    def _commit_release(self, key, specific):
        with self._debounce_lock:
            if self._pending_release.pop(specific, None) is None:
                return  # already swallowed by a repeat press
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
