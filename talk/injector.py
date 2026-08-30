"""Typing the transcript into whatever window has focus.

Two strategies, chosen by the user:
  paste - put the text on the clipboard, send Ctrl+V, then put the
          user's original clipboard back. Instant, works in most apps.
  type  - send the text key by key. Slower, and layout-sensitive, but
          works in apps where Ctrl+V means something else.

Both need to press keys in another app's window, and how that is done
depends entirely on the display server:

  Wayland - only the compositor may synthesise input, so everything goes
            through the RemoteDesktop portal (inject_portal.py). The user
            grants permission once, and the desktop must provide that
            portal: GNOME and KDE do, most others do not.

  X11     - any client may do it directly through XTEST
            (inject_xtest.py). No portal, no permission, works on every
            X11 desktop including Cinnamon, XFCE, MATE and bare window
            managers.

The choice is made from the session, never from which GTK backend
happens to be in use: the AppImage runs its windows through XWayland even
on a Wayland session, so GTK says "X11" where XTEST would silently do
nothing for Wayland-native windows.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

from . import portal, session
from .inject_portal import PortalInjector

log = logging.getLogger("talk.injector")

_backend = None


class InjectionUnavailable(RuntimeError):
    """No way to type into other windows on this desktop."""


def setup(config, on_ready=None, on_lost=None):
    """Prepare the injection backend. Call once at startup.

    On Wayland the portal session is started here rather than lazily: it
    puts the compositor's consent prompt right after launch, where the
    user connects it with having just started Talk, instead of
    springing it mid-sentence on the first dictation. On X11 there is
    nothing to consent to, so this is immediate.
    """
    global _backend
    if session.is_wayland():
        if not portal.available() or not portal.has_interface(
                "org.freedesktop.portal.RemoteDesktop"):
            raise InjectionUnavailable(
                "this desktop's portal does not provide RemoteDesktop")
        log.info("injection backend: RemoteDesktop portal (%s)",
                 session.describe())
        _backend = PortalInjector(config, on_lost=on_lost)
        _backend.start(on_ready)
        return

    try:
        from .inject_xtest import XTestInjector
        _backend = XTestInjector()
    except Exception as exc:
        raise InjectionUnavailable("XTEST is unavailable: {}".format(exc))
    log.info("injection backend: XTEST (%s)", session.describe())
    _backend.start(on_ready)


def shutdown():
    if _backend is not None:
        _backend.stop()


def ready():
    return _backend is not None and _backend.ready


def _clipboard():
    return Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)


def _paste(text, done, keep=False):
    """Runs on the GTK main loop: swap clipboard, paste, restore.

    The transcript is pasted rather than typed because a portal keystroke
    is a round trip: sending a sentence key by key runs at a few
    characters a second, while this is one Ctrl+V.

    With `keep`, the transcript is left on the clipboard afterwards
    instead of the previous contents being put back. That is the safety
    net for the commonest way a dictation goes nowhere: nothing was
    focused when you spoke, so the paste lands in no field at all and the
    words appear lost. They are not lost — they are one right-click away.
    """
    clipboard = _clipboard()
    original = clipboard.wait_for_text()
    clipboard.set_text(text, -1)
    clipboard.store()

    def restore(ok):
        def apply():
            if not keep and original is not None:
                _clipboard().set_text(original, -1)
                _clipboard().store()
            done(ok)
            return False
        GLib.idle_add(apply)

    if not ready():
        log.warning("injection not ready; dropping %d chars", len(text))
        restore(False)
        return False

    _backend.send_paste(restore)
    return False


def _type(text, done, keep=False):
    if keep:
        # Typed out rather than pasted, so the clipboard was never
        # involved; put it there anyway, for the same reason.
        clipboard = _clipboard()
        clipboard.set_text(text, -1)
        clipboard.store()
    if not ready():
        log.warning("injection not ready; dropping %d chars", len(text))
        GLib.idle_add(lambda: (done(False), False)[1])
        return
    _backend.send_text(text, done)


def inject(text, config, on_done):
    """Enter `text` into the focused window. on_done(ok) on main loop."""
    if not text:
        GLib.idle_add(lambda: (on_done(True), False)[1])
        return
    keep = bool(config.get("keep_on_clipboard"))
    if config.get("injection") == "type":
        _type(text + " ", on_done, keep)
    else:
        GLib.idle_add(_paste, text + " ", on_done, keep)


def selection_available():
    """Whether reading the on-screen selection can work here.

    It cannot on Wayland: letting a background process read whatever you
    have highlighted is exactly what the security model exists to stop.
    Kept as a function so the correction popup can adapt rather than
    silently do nothing.
    """
    return not session.is_wayland()


def read_primary_selection():
    """The highlighted text, where the display server allows reading it.

    Always None on Wayland — callers should check selection_available()
    and offer the user another way in.
    """
    if not selection_available():
        return None
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
    return clipboard.wait_for_text()
