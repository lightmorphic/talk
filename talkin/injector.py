"""Typing the transcript into whatever window has focus.

Two strategies, chosen by the user:
  paste - put the text on the clipboard, send Ctrl+V, then put the
          user's original clipboard back. Instant, works in most apps.
  type  - synthesise real keystrokes. Slower but works everywhere,
          including terminals where Ctrl+V means something else.

Both need a way to press keys in another app's window, and that differs
by display server:

  X11     - XTEST through pynput, as it always has.
  Wayland - the RemoteDesktop portal (see inject_portal.py); XTEST is not
            available and silently does nothing there.

The backend is chosen once at startup by setup(). If the Wayland portal
is unavailable we fall through to the X11 path rather than failing hard:
on XWayland that still reaches X11 apps, which is better than nothing.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

from pynput.keyboard import Controller, Key

from . import portal, session
from .inject_portal import PortalInjector

log = logging.getLogger("talkin.injector")

_keyboard = Controller()
_portal_injector = None


def setup(config, on_ready=None):
    """Pick and prepare the injection backend. Call once at startup.

    Starting the portal session here rather than lazily is deliberate: it
    puts the compositor's consent dialog right after launch, where the
    user connects it to having just started Talkin, instead of springing
    it mid-sentence on the first dictation.
    """
    global _portal_injector
    if not session.is_wayland():
        log.info("injection backend: XTEST (%s)", session.describe())
        if on_ready:
            on_ready(True)
        return

    if not portal.available() or not portal.has_interface(
            "org.freedesktop.portal.RemoteDesktop"):
        log.warning("Wayland session but no RemoteDesktop portal; falling "
                    "back to XTEST, which only reaches XWayland apps")
        if on_ready:
            on_ready(False)
        return

    log.info("injection backend: RemoteDesktop portal (%s)",
             session.describe())
    _portal_injector = PortalInjector(config)
    _portal_injector.start(on_ready)


def shutdown():
    if _portal_injector is not None:
        _portal_injector.stop()


def using_portal():
    return _portal_injector is not None and _portal_injector.ready


def _clipboard():
    return Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)


def _paste(text, done):
    """Runs on the GTK main loop: swap clipboard, paste, restore."""
    clipboard = _clipboard()
    original = clipboard.wait_for_text()
    clipboard.set_text(text, -1)
    clipboard.store()

    def restore(ok):
        def apply():
            if original is not None:
                _clipboard().set_text(original, -1)
                _clipboard().store()
            done(ok)
            return False
        GLib.idle_add(apply)

    if using_portal():
        _portal_injector.send_paste(restore)
        return False

    def press_paste():
        time.sleep(0.08)  # let the clipboard owner change settle
        with _keyboard.pressed(Key.ctrl):
            _keyboard.press("v")
            _keyboard.release("v")
        time.sleep(0.25)  # give the app time to read the clipboard
        restore(True)

    threading.Thread(target=press_paste, daemon=True).start()
    return False


def _type(text, done):
    """Runs on its own thread: send the text as real keystrokes."""
    if using_portal():
        _portal_injector.send_text(text, done)
        return

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


def selection_available():
    """Whether reading the on-screen selection can work at all here.

    Wayland has no equivalent of the X11 PRIMARY selection for an
    unfocused app: letting any background process read whatever you have
    highlighted is precisely what the security model exists to prevent.
    """
    return not session.is_wayland()


def read_primary_selection():
    """The text currently highlighted anywhere on screen (X11 PRIMARY).

    Returns None on Wayland, where this is not permitted — callers should
    check selection_available() and offer the user another way in.
    """
    if not selection_available():
        return None
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
    return clipboard.wait_for_text()
