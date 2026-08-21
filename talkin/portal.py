"""Shared plumbing for the XDG desktop portals.

The portals are how a Wayland app asks the compositor for things it is no
longer allowed to just take: global hotkeys and synthetic keystrokes. Every
portal call follows the same awkward shape, so it lives here once:

  1. you pick a handle_token,
  2. you subscribe to a Response signal on a path derived from that token
     and your bus name,
  3. THEN you call the method,
  4. the real answer arrives later on that signal, not as a return value.

Subscribing before calling matters: the portal can answer immediately and
we would miss a signal we had not subscribed to yet.

All calls are asynchronous. Talkin's GTK main loop must never block on a
portal, because several of these calls wait on a human clicking a consent
dialog — a synchronous call there would freeze the tray and the settings
window for as long as the dialog is up.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import glob
import logging
import os
import sys

from gi.repository import Gio, GLib

log = logging.getLogger("talkin.portal")

BUS_NAME = "org.freedesktop.portal.Desktop"
OBJECT_PATH = "/org/freedesktop/portal/desktop"
REQUEST_IFACE = "org.freedesktop.portal.Request"
REGISTRY_IFACE = "org.freedesktop.host.portal.Registry"

# Reverse-DNS id matching packaging/uk.co.lightmorphic.Talkin.appdata.xml.
# Used only if we cannot find the .desktop file we were actually launched
# from (AppImage integrators name that file themselves).
DEFAULT_APP_ID = "uk.co.lightmorphic.Talkin"

_counter = [0]


class PortalError(Exception):
    """A portal call failed, was cancelled, or is unavailable here."""


def _connection():
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def available():
    """True if the desktop portal is running and reachable."""
    try:
        conn = _connection()
        conn.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner",
            GLib.Variant("(s)", (BUS_NAME,)), GLib.VariantType("(b)"),
            Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
    except Exception:
        log.info("no session bus or portal; portal backends unavailable")
        return False
    return True


def has_interface(iface):
    """True if the portal exports `iface` (e.g. it has a working backend).

    Reading the interface's `version` property is the cheap probe: the
    property only resolves if the interface is actually exported.
    """
    try:
        _connection().call_sync(
            BUS_NAME, OBJECT_PATH, "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", (iface, "version")), GLib.VariantType("(v)"),
            Gio.DBusCallFlags.NONE, 2000, None)
        return True
    except Exception:
        return False


def _application_dirs():
    """Every directory the desktop spec says .desktop files live in."""
    dirs = [os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "applications")]
    system = os.environ.get("XDG_DATA_DIRS",
                            "/usr/local/share:/usr/share")
    dirs.extend(os.path.join(d, "applications")
                for d in system.split(":") if d)
    return dirs


def app_id_candidates():
    """Ids to try with the portal, best first.

    The portal validates an id against installed .desktop files and
    refuses anything it cannot find ("App info not found"), so guessing a
    tidy reverse-DNS id is not enough — it has to be one that exists on
    THIS machine. AppImage integrators (Gear Lever, appimaged) each invent
    their own filename, so the installed entry is discovered rather than
    assumed.
    """
    candidates = []

    def add(value):
        if value and value not in candidates:
            candidates.append(value)

    # 1. Whichever entry actually launches this binary.
    executable = os.environ.get("APPIMAGE") or os.path.abspath(sys.argv[0])
    named_talkin = []
    for directory in _application_dirs():
        for path in glob.glob(os.path.join(directory, "*.desktop")):
            name = os.path.basename(path)[:-len(".desktop")]
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    body = f.read()
            except OSError:
                continue
            if executable and executable in body:
                add(name)
            elif "talkin" in name.lower():
                named_talkin.append(name)

    # 2. Otherwise any installed entry that looks like ours.
    for name in named_talkin:
        add(name)

    # 3. Last resort, so a first run with no integration still tries.
    add(DEFAULT_APP_ID)
    return candidates


def app_id():
    """The single best id to claim (first candidate)."""
    return app_id_candidates()[0]


def register_app_id(name=None):
    """Claim an app id so identity-gated portals will talk to us.

    GlobalShortcuts rejects unidentified callers outright ("An app id is
    required"). A Flatpak gets its id from its sandbox; a plain host
    binary like an AppImage has none until it registers one here.

    Best-effort: RemoteDesktop does not need an id, and older portals have
    no Registry at all, so a failure must not stop the rest of Talkin.
    """
    names = [name] if name else app_id_candidates()
    last_error = None
    for candidate in names:
        try:
            _connection().call_sync(
                BUS_NAME, OBJECT_PATH, REGISTRY_IFACE, "Register",
                GLib.Variant("(sa{sv})", (candidate, {})), None,
                Gio.DBusCallFlags.NONE, 5000, None)
            log.info("registered app id %r with the portal", candidate)
            return candidate
        except Exception as exc:
            # "App info not found" just means this id has no .desktop file
            # here; try the next candidate rather than giving up.
            last_error = exc
            log.debug("app id %r rejected: %s", candidate, exc)
    log.warning("could not register any app id (tried %s): %s",
                ", ".join(names), last_error)
    return None


def _request_path(conn, token):
    sender = conn.get_unique_name()[1:].replace(".", "_")
    return "{}/request/{}/{}".format(OBJECT_PATH, sender, token)


def call(iface, method, signature, values, on_ok, on_error=None):
    """Call a portal method that answers with a Request.

    `values` must end with the options dict; we inject handle_token into
    it. `on_ok(results_dict)` fires on success, `on_error(PortalError)` on
    failure or user cancellation. Both run on the GLib main loop.
    """
    conn = _connection()
    _counter[0] += 1
    token = "talkin_{}_{}".format(os.getpid(), _counter[0])

    def fail(message):
        log.warning("%s.%s: %s", iface.rsplit(".", 1)[-1], method, message)
        if on_error is not None:
            on_error(PortalError(message))

    subscription = [None]

    def on_response(_conn, _sender, _path, _iface, _signal, params):
        code, results = params.unpack()
        if subscription[0] is not None:
            conn.signal_unsubscribe(subscription[0])
            subscription[0] = None
        if code == 0:
            on_ok(results)
        elif code == 1:
            fail("cancelled by the user")
        else:
            fail("failed (code {})".format(code))

    # Subscribe BEFORE calling: the portal may answer immediately.
    subscription[0] = conn.signal_subscribe(
        BUS_NAME, REQUEST_IFACE, "Response", _request_path(conn, token),
        None, Gio.DBusSignalFlags.NONE, on_response)

    options = values[-1]
    options["handle_token"] = GLib.Variant("s", token)

    def on_returned(source, result):
        try:
            source.call_finish(result)
        except Exception as exc:
            if subscription[0] is not None:
                conn.signal_unsubscribe(subscription[0])
                subscription[0] = None
            fail(str(exc))

    conn.call(BUS_NAME, OBJECT_PATH, iface, method,
              GLib.Variant(signature, values), None,
              Gio.DBusCallFlags.NONE, -1, None, on_returned)


def call_plain(iface, method, signature, values):
    """Call a portal method that answers directly, with no Request.

    Used for the high-frequency input calls (NotifyKeyboard*), which
    return immediately and must stay in order — so this one is
    synchronous by design, and is only ever called off the main loop.
    """
    try:
        _connection().call_sync(
            BUS_NAME, OBJECT_PATH, iface, method,
            GLib.Variant(signature, values), None,
            Gio.DBusCallFlags.NONE, 5000, None)
    except Exception as exc:
        raise PortalError("{}.{} failed: {}".format(iface, method, exc))


def close_session(session_handle):
    """Close a portal session. Best-effort: the portal reaps sessions when
    our bus connection drops anyway, so a failure here is not worth
    surfacing. Note this targets the SESSION's object path, not the
    desktop object every other call uses."""
    if not session_handle:
        return
    try:
        _connection().call_sync(
            BUS_NAME, session_handle, "org.freedesktop.portal.Session",
            "Close", None, None, Gio.DBusCallFlags.NONE, 2000, None)
    except Exception as exc:
        log.debug("closing session %s failed: %s", session_handle, exc)


def subscribe(iface, signal, handler):
    """Listen for a portal signal. Returns an id for unsubscribe().

    Deliberately not filtered by object path: portals differ on which
    object they emit from, and an over-tight filter shows up as total
    silence that looks exactly like "the feature never fired".
    """
    conn = _connection()
    return conn.signal_subscribe(BUS_NAME, iface, signal, None, None,
                                 Gio.DBusSignalFlags.NONE, handler)


def unsubscribe(subscription_id):
    if subscription_id is None:
        return
    try:
        _connection().signal_unsubscribe(subscription_id)
    except Exception:
        pass
