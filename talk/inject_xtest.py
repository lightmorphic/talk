"""Typing into the focused window on X11, without asking permission.

X11 lets any client synthesise keystrokes through the XTEST extension.
That is a poor security decision and the reason Wayland exists, but on an
X11 session it is simply how things work — and it means no portal, no
consent dialog, and nothing to grant before the first dictation.

This is the backend for X11 desktops. Wayland sessions use the
RemoteDesktop portal instead (inject_portal.py); XTEST is not offered
there because it silently does nothing for Wayland-native windows while
appearing to succeed, which is worse than refusing.

The interface deliberately mirrors PortalInjector so injector.py can hold
either one without caring which.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import threading
import time

from gi.repository import GLib

log = logging.getLogger("talk.inject_xtest")


class XTestInjector:
    """Keyboard injection through XTEST, via pynput."""

    def __init__(self):
        from pynput.keyboard import Controller
        self._keyboard = Controller()
        self._ok = True

    @property
    def ready(self):
        # Nothing to negotiate: if the controller was built, X will
        # accept keystrokes. There is no consent step to wait for.
        return self._ok

    @property
    def failed(self):
        return not self._ok

    def start(self, on_ready=None):
        if on_ready:
            GLib.idle_add(lambda: (on_ready(True), False)[1])

    def stop(self):
        self._ok = False

    # -- key sending -----------------------------------------------------

    def send_paste(self, on_done):
        """One Ctrl+V. The caller owns putting the text on the clipboard."""
        from pynput.keyboard import Key

        def worker():
            ok = True
            try:
                # Let the clipboard ownership change settle before the
                # paste, or the target app reads the previous contents.
                time.sleep(0.08)
                with self._keyboard.pressed(Key.ctrl):
                    self._keyboard.press("v")
                    self._keyboard.release("v")
                time.sleep(0.25)
            except Exception:
                log.exception("XTEST paste failed")
                ok = False
            GLib.idle_add(lambda: (on_done(ok), False)[1])

        threading.Thread(target=worker, daemon=True).start()

    def send_text(self, text, on_done):
        """Type the text out key by key, for the explicit `type` mode."""
        def worker():
            ok = True
            try:
                self._keyboard.type(text)
            except Exception:
                log.exception("XTEST typing failed")
                ok = False
            GLib.idle_add(lambda: (on_done(ok), False)[1])

        threading.Thread(target=worker, daemon=True).start()
