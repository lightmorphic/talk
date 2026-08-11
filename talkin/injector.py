"""Typing the transcript into whatever window has focus.

Two strategies:
  paste - put the text on the clipboard, send Ctrl+V, then put the
          user's original clipboard back. Instant, works in most apps.
  type  - synthesise real keystrokes via XTEST. Slower but works
          everywhere, including terminals where Ctrl+V means
          something else.
"""

import logging
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

from pynput.keyboard import Controller, Key

log = logging.getLogger("talkin.injector")

_keyboard = Controller()


def _clipboard():
    return Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)


def _paste(text, done):
    """Runs on the GTK main loop: swap clipboard, paste, restore."""
    clipboard = _clipboard()
    original = clipboard.wait_for_text()
    clipboard.set_text(text, -1)
    clipboard.store()

    def press_paste():
        time.sleep(0.08)  # let the clipboard owner change settle
        with _keyboard.pressed(Key.ctrl):
            _keyboard.press("v")
            _keyboard.release("v")
        time.sleep(0.25)  # give the app time to read the clipboard

        def restore():
            if original is not None:
                _clipboard().set_text(original, -1)
                _clipboard().store()
            done(True)
            return False

        GLib.idle_add(restore)

    threading.Thread(target=press_paste, daemon=True).start()
    return False


def _type(text, done):
    """Runs on its own thread: send the text as real keystrokes."""
    def worker():
        ok = True
        try:
            _keyboard.type(text)
        except Exception:
            log.exception("typing injection failed")
            ok = False
        GLib.idle_add(lambda: (done(ok), False)[1])

    threading.Thread(target=worker, daemon=True).start()


def inject(text, config, on_done):
    """Enter `text` into the focused window. on_done(ok) on main loop."""
    if not text:
        GLib.idle_add(lambda: (on_done(True), False)[1])
        return
    if config.get("injection") == "type":
        _type(text + " ", on_done)
    else:
        GLib.idle_add(_paste, text + " ", on_done)


def read_primary_selection():
    """The text currently highlighted anywhere on screen (X11 PRIMARY)."""
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
    return clipboard.wait_for_text()
