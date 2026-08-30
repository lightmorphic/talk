"""Typing into the focused window through the RemoteDesktop portal.

XTEST does not exist on Wayland, and the wlroots virtual-keyboard protocol
that other dictation tools reach for is deliberately not implemented by
Mutter, so it only ever works on some desktops. The RemoteDesktop portal
is the one route the compositor itself sanctions, and it needs no
/dev/uinput access and no adding the user to the `input` group.

Two things learned from spiking this against GNOME 50:

  * Consent persists. Asking for persist_mode=2 makes Start return a
    restore_token; storing it and handing it back next launch turns a
    dialog-every-start into a single one-off prompt.

  * Never send a transcript key by key. Each keysym is a round trip, which
    lands around 3-4 characters a second — fine for a spike, useless for a
    sentence. The paste path puts the text on the clipboard and sends ONE
    Ctrl+V, which is why paste is the default strategy.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import threading
import time

from gi.repository import GLib

from . import portal

log = logging.getLogger("talk.inject_portal")

IFACE = "org.freedesktop.portal.RemoteDesktop"

DEVICE_KEYBOARD = 1
PERSIST_UNTIL_REVOKED = 2

PRESSED = 1
RELEASED = 0

# X keysyms. Latin-1 characters are their own code point, which covers
# every printable character; these are the named ones we need.
KEYSYM_CONTROL_L = 0xFFE3
KEYSYM_SHIFT_L = 0xFFE1
KEYSYM_U = 0x75
KEYSYM_V = 0x76
KEYSYM_RETURN = 0xFF0D
KEYSYM_SPACE = 0x20

# Config key holding the portal's restore token.
RESTORE_TOKEN_KEY = "wayland_restore_token"


class PortalInjector:
    """Keyboard injection over RemoteDesktop.

    A single long-lived session is opened at startup so the consent prompt
    happens once, at a moment the user can connect with having just
    launched Talk — rather than mid-sentence on first dictation.
    """

    def __init__(self, config, on_lost=None):
        self.config = config
        self._session = None
        self._starting = False
        self._failed = False
        self._waiters = []          # callbacks queued while starting
        self._lock = threading.Lock()
        self._on_lost = on_lost
        self._closed_sub = None
        self._reconnecting = False
        self._started = False

    # -- availability ----------------------------------------------------

    @property
    def ready(self):
        """True only after Start succeeded and a keyboard was granted.

        Creating a session is not permission. The session handle exists
        the moment the portal accepts CreateSession, which is before the
        user has been asked anything — reporting ready then made the app
        announce itself working, and the first-run window close itself,
        while it could not type a single word.
        """
        return self._session is not None and self._started

    @property
    def failed(self):
        return self._failed

    # -- session lifecycle -----------------------------------------------

    def start(self, on_ready=None):
        """Open the session, restoring previous consent when we have it."""
        if self._session is not None and self._started:
            if on_ready:
                on_ready(True)
            return
        if on_ready:
            self._waiters.append(on_ready)
        if self._starting:
            return
        self._starting = True

        def created(results):
            self._session = results.get("session_handle")
            self._select_devices()

        portal.call(IFACE, "CreateSession", "(a{sv})", ({
            "session_handle_token": GLib.Variant("s", "talk_inject"),
        },), created, self._start_failed)

    def _select_devices(self):
        options = {
            "types": GLib.Variant("u", DEVICE_KEYBOARD),
            "persist_mode": GLib.Variant("u", PERSIST_UNTIL_REVOKED),
        }
        token = self.config.get(RESTORE_TOKEN_KEY)
        if token:
            # With a valid token the compositor may skip the dialog
            # entirely; an expired or revoked one just means it asks again.
            options["restore_token"] = GLib.Variant("s", token)
            log.info("restoring previous input permission")

        portal.call(IFACE, "SelectDevices", "(oa{sv})",
                    (self._session, options),
                    lambda _results: self._start(), self._start_failed)

    def _start(self):
        def started(results):
            token = results.get("restore_token")
            if token and token != self.config.get(RESTORE_TOKEN_KEY):
                self.config.update({RESTORE_TOKEN_KEY: token})
                log.info("stored input permission token for next launch")
            devices = results.get("devices", 0)
            if not devices & DEVICE_KEYBOARD:
                log.warning("portal granted devices=%s, no keyboard", devices)
                self._start_failed(portal.PortalError("no keyboard granted"))
                return
            self._starting = False
            self._reconnecting = False
            self._started = True
            self._watch_for_close()
            log.info("keyboard injection ready via the portal")
            self._flush(True)

        portal.call(IFACE, "Start", "(osa{sv})", (self._session, "", {}),
                    started, self._start_failed)

    # -- losing the session ----------------------------------------------

    def _watch_for_close(self):
        """Notice the session ending and rebuild it.

        The desktop shows a status indicator while an app can control
        input, and clicking it stops the session — which is the user
        exercising exactly the control the portal exists to give them.
        Without this, Talk kept running and silently never typed
        again, which reads as the app having crashed.
        """
        if self._closed_sub is not None:
            return
        self._closed_sub = portal.subscribe(
            "org.freedesktop.portal.Session", "Closed", self._on_closed)

    def _on_closed(self, _conn, _sender, path, _iface, _signal, _params):
        if self._session is None or path != self._session:
            return
        log.warning("input permission was withdrawn; session closed")
        self._session = None
        self._started = False
        if self._on_lost is not None:
            self._on_lost()
        # One quiet attempt to re-establish. With a still-valid restore
        # token this is silent; if the user revoked it, the compositor
        # asks again, which is the right thing to happen.
        if not self._reconnecting:
            self._reconnecting = True
            GLib.timeout_add_seconds(2, self._retry)

    def _retry(self):
        if self._session is None:
            log.info("re-requesting input permission")
            self.start()
        return False  # one shot

    def _start_failed(self, exc):
        log.warning("portal injection unavailable: %s", exc)
        self._starting = False
        self._reconnecting = False
        self._started = False
        self._failed = True
        self._session = None
        self._flush(False)

    def _flush(self, ok):
        waiters, self._waiters = self._waiters, []
        for callback in waiters:
            callback(ok)

    def stop(self):
        self._started = False
        portal.unsubscribe(self._closed_sub)
        self._closed_sub = None
        portal.close_session(self._session)
        self._session = None

    # -- key sending -----------------------------------------------------

    def _keysym(self, keysym, state):
        portal.call_plain(IFACE, "NotifyKeyboardKeysym", "(oa{sv}iu)",
                          (self._session, {}, keysym, state))

    def _tap(self, keysym, modifiers=()):
        for modifier in modifiers:
            self._keysym(modifier, PRESSED)
        self._keysym(keysym, PRESSED)
        self._keysym(keysym, RELEASED)
        for modifier in reversed(modifiers):
            self._keysym(modifier, RELEASED)

    def send_paste(self, on_done):
        """One Ctrl+V. The caller owns putting the text on the clipboard."""
        def worker():
            ok = True
            try:
                # Let the clipboard ownership change settle before the
                # paste, or the target app reads the previous contents.
                time.sleep(0.08)
                self._tap(KEYSYM_V, modifiers=(KEYSYM_CONTROL_L,))
                time.sleep(0.25)
            except Exception:
                log.exception("portal paste failed")
                ok = False
            GLib.idle_add(lambda: (on_done(ok), False)[1])

        threading.Thread(target=worker, daemon=True).start()

    def send_text(self, text, on_done):
        """Type text key by key. Slow — only for the explicit `type` mode.

        Keysym injection follows the active keyboard layout, so characters
        absent from that layout (accents on a layout without them, and on
        AZERTY even digits and capitals) can come out wrong or not at all.
        Anything outside Latin-1 goes through the layout-independent
        Ctrl+Shift+U unicode escape instead.
        """
        def worker():
            ok = True
            try:
                for char in text:
                    code = ord(char)
                    if char == "\n":
                        self._tap(KEYSYM_RETURN)
                    elif code < 0x100:
                        self._tap(code)
                    else:
                        self._unicode_escape(code)
                    time.sleep(0.004)
            except Exception:
                log.exception("portal typing failed")
                ok = False
            GLib.idle_add(lambda: (on_done(ok), False)[1])

        threading.Thread(target=worker, daemon=True).start()

    def _unicode_escape(self, codepoint):
        """Ctrl+Shift+U <hex> Enter — works regardless of keyboard layout."""
        self._keysym(KEYSYM_CONTROL_L, PRESSED)
        self._keysym(KEYSYM_SHIFT_L, PRESSED)
        self._keysym(KEYSYM_U, PRESSED)
        self._keysym(KEYSYM_U, RELEASED)
        self._keysym(KEYSYM_SHIFT_L, RELEASED)
        self._keysym(KEYSYM_CONTROL_L, RELEASED)
        for digit in "{:x}".format(codepoint):
            self._tap(ord(digit))
        self._tap(KEYSYM_RETURN)
