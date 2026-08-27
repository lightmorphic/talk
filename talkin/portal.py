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


# The one session-bus connection for this process, held for its whole
# lifetime. This reference is NOT optional: an app id is registered
# against a connection, and g_bus_get_sync() hands back a fresh
# connection — with a new unique name — once the previous one has no
# references left. Dropping it meant the id was registered on a
# connection that no longer existed by the time a session was requested,
# so GlobalShortcuts answered "an app id is required" and every hotkey
# died. It also came and went with timing, which is what made this look
# like general instability rather than one missing reference.
_conn = [None]
_bootstrapped = [False]


def _connection():
    """The session bus, with our app id claimed before anything else.

    ORDER MATTERS. The portal associates a connection with an app id on
    first contact, and a Register afterwards answers "Connection already
    associated with an application ID" while GlobalShortcuts keeps
    refusing with "an app id is required". Probing the portal first —
    even just reading a version property — is enough to pin the
    connection anonymously and lose every hotkey. So registration is
    bootstrapped here, on the first use of the bus, rather than left to
    whichever caller happens to run first.
    """
    if _conn[0] is None:
        _conn[0] = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        _conn[0].set_exit_on_close(False)
    conn = _conn[0]
    if not _bootstrapped[0]:
        _bootstrapped[0] = True
        try:
            _register_first(conn)
        except Exception as exc:
            log.debug("app id bootstrap failed: %s", exc)
    return conn


def _register_first(conn):
    ensure_desktop_entry()
    for candidate in app_id_candidates():
        try:
            conn.call_sync(
                BUS_NAME, OBJECT_PATH, REGISTRY_IFACE, "Register",
                GLib.Variant("(sa{sv})", (candidate, {})), None,
                Gio.DBusCallFlags.NONE, 5000, None)
            log.info("registered app id %r on connection %s", candidate,
                     conn.get_unique_name())
            _registered["id"] = candidate
            _registered["tried"].append(candidate)
            return candidate
        except Exception as exc:
            _registered["tried"].append(candidate)
            log.debug("app id %r rejected: %s", candidate, exc)
    log.warning("no app id could be registered; hotkeys will be refused")
    return None


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


def interface_version(iface):
    """The portal's version of `iface`, or 0 if it is not exported.

    Worth checking before using anything newer than version 1: a method
    can be listed in the interface and still answer UnknownMethod,
    because the published interface describes the newest version while
    the running backend may implement an older one.
    """
    try:
        result = _connection().call_sync(
            BUS_NAME, OBJECT_PATH, "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", (iface, "version")), GLib.VariantType("(v)"),
            Gio.DBusCallFlags.NONE, 2000, None)
        return int(result.unpack()[0])
    except Exception:
        return 0


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


def _existing_entries(executable):
    """Desktop entry names, other than ours, that already launch us.

    Logged as well as used, because an entry left over from an older
    install points at an older AppImage — which looks to the user like
    the app misbehaving when they happen to click the wrong icon.
    """
    found = {}
    # Match on the app's stem, not on this build's exact filename. An
    # installed copy is called talkin.appimage while a locally built one
    # is Talkin-x86_64.AppImage: comparing full filenames misses that
    # they are the same app, and the entry we wrote then survives
    # alongside the installed one as a second icon.
    stem = DEFAULT_APP_ID.rsplit(".", 1)[-1].lower()   # "talkin"
    for directory in _application_dirs():
        for path in glob.glob(os.path.join(directory, "*.desktop")):
            name = os.path.basename(path)[:-len(".desktop")]
            if name == DEFAULT_APP_ID:
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    body = f.read()
            except OSError:
                continue
            if executable in body:
                found[name] = "this build"
            elif stem in name.lower() or stem in body.lower():
                found[name] = "another copy"
    for name, which in found.items():
        if which == "another copy":
            log.warning("desktop entry %r points at a different copy of "
                        "Talkin; launching it runs that one, not this",
                        name)
    return set(found)


def ensure_desktop_entry():
    """Guarantee there is a .desktop file matching DEFAULT_APP_ID.

    Identity-gated portals (GlobalShortcuts) validate an app id against
    installed .desktop files and refuse anything they cannot find. An
    AppImage that the user has not "integrated" has no entry at all, so
    without this the hotkeys simply never work — and the failure is a
    single line in a log, which is exactly how it went unnoticed.

    Writing one is what desktop integration would have done anyway. Only
    ever writes our own file, and only when running as an AppImage (a
    source checkout has no stable path worth advertising).
    """
    appimage = os.environ.get("APPIMAGE")
    if not appimage:
        return None
    directory = os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "applications")
    path = os.path.join(directory, DEFAULT_APP_ID + ".desktop")

    # If the desktop already has an entry for us, use it and do NOT add a
    # second one: two entries means two launcher icons for one app, and
    # the user cannot tell which is which — worse, an older entry may
    # still point at a previous copy of the AppImage. Ours exists only to
    # cover the case where nothing else does.
    existing = _existing_entries(appimage)
    if existing:
        log.info("desktop entry already present: %s",
                 ", ".join(sorted(existing)))
        if os.path.exists(path) and os.path.basename(path)[:-8] not in existing:
            try:
                os.remove(path)
                log.info("removed our duplicate entry %s", path)
            except OSError:
                pass
        return None
    icon = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "talkin.png")
    entry = ("[Desktop Entry]\n"
             "Type=Application\n"
             "Name=Talkin\n"
             "Comment=Private, on-device dictation for Wayland desktops\n"
             "Exec={}\n"
             "Icon={}\n"
             "Categories=Utility;Accessibility;\n"
             "Terminal=false\n"
             "StartupWMClass=talkin\n").format(appimage, icon)
    try:
        os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(entry)
        log.info("wrote desktop entry %s so the portal can identify us", path)
        return path
    except OSError as exc:
        log.warning("could not write a desktop entry: %s", exc)
        return None


def app_id_candidates():
    """Ids to try with the portal, best first.

    The portal validates an id against installed .desktop files and
    refuses anything it cannot find ("App info not found"), so a tidy
    reverse-DNS id is not enough on its own — it has to name a file that
    exists on THIS machine. AppImage integrators (Gear Lever, appimaged)
    each invent their own filename, so entries are discovered by the
    executable they launch rather than by guessing at their names.

    Matching on a name substring was tried and was a bug: the filter
    looked for "talkin", which is not a substring of "talkin", so this
    app skipped its own desktop file and had no identity at all.
    """
    candidates = []

    def add(value):
        if value and value not in candidates:
            candidates.append(value)

    executable = os.environ.get("APPIMAGE") or os.path.abspath(sys.argv[0])
    ours = os.path.basename(executable).lower()
    stem = DEFAULT_APP_ID.rsplit(".", 1)[-1].lower()   # "talkin"
    by_name = []
    for directory in _application_dirs():
        for path in glob.glob(os.path.join(directory, "*.desktop")):
            name = os.path.basename(path)[:-len(".desktop")]
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    body = f.read()
            except OSError:
                continue
            # 1. The entry that actually launches this binary — the only
            #    match that cannot be a coincidence.
            if name == DEFAULT_APP_ID:
                continue   # ours; appended last, see below
            if executable and executable in body:
                add(name)
            elif ours and ours in body.lower():
                add(name)
            elif stem in name.lower():
                by_name.append(name)

    # 2. Otherwise any installed entry whose name looks like ours.
    for name in by_name:
        add(name)

    # 3. Our own entry last. Registering against it succeeds, but a
    #    just-written file is not in the desktop database yet, and a
    #    backend that re-resolves the id then refuses the session with
    #    the same "an app id is required" as having no id at all. An
    #    entry the system already knows about is always the safer claim.
    add(DEFAULT_APP_ID)
    return candidates


_registered = {"id": None, "tried": []}


def registered_app_id():
    return _registered["id"]


def register_next_app_id():
    """Claim the next candidate after the one currently registered.

    Registration succeeding does not prove the id is usable — see above —
    so callers that get refused anyway can walk down the list.
    """
    remaining = [c for c in app_id_candidates() if c not in _registered["tried"]]
    if not remaining:
        return None
    return register_app_id(remaining[0])


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
    # Create our own entry before looking for one, so a fresh AppImage on
    # a machine with no desktop integration still has an identity to claim
    # — without it GlobalShortcuts refuses us and every hotkey is dead.
    ensure_desktop_entry()
    names = [name] if name else app_id_candidates()
    last_error = None
    for candidate in names:
        try:
            _connection().call_sync(
                BUS_NAME, OBJECT_PATH, REGISTRY_IFACE, "Register",
                GLib.Variant("(sa{sv})", (candidate, {})), None,
                Gio.DBusCallFlags.NONE, 5000, None)
            log.info("registered app id %r with the portal", candidate)
            _registered["id"] = candidate
            if candidate not in _registered["tried"]:
                _registered["tried"].append(candidate)
            return candidate
        except Exception as exc:
            # "App info not found" just means this id has no .desktop file
            # here; try the next candidate rather than giving up.
            last_error = exc
            if candidate not in _registered["tried"]:
                _registered["tried"].append(candidate)
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
    log.debug("calling %s.%s on connection %s", iface.rsplit(".", 1)[-1],
              method, conn.get_unique_name())

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
