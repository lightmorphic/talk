"""Global hotkeys through the GlobalShortcuts portal (Wayland).

On Wayland the compositor owns every shortcut. That has three
consequences this module is built around, all learned the hard way:

  * **The compositor is the source of truth, not our config.** Once a
    shortcut is bound, the user rebinds it in the desktop's own shortcut
    editor, and what they choose there wins. Talkin's stored combo is
    only a *suggested default* for the first bind. Trying to capture a
    key inside our own Settings window cannot work: the compositor grabs
    the bound combo and fires the shortcut instead of delivering the
    keystroke to us, so the field never sees it. Settings therefore shows
    what is bound and offers configure(), which opens the desktop's
    editor for all our shortcuts at once.

  * **Activated repeats while a key is held** — once, then again after
    ~500ms, then ~33 times a second — with exactly one Deactivated on
    release. Treating each Activated as a fresh press restarts the
    recording continuously. But de-duplicating naively is worse: if a
    single Deactivated is ever missed, the shortcut stays "held" forever
    and every later press is swallowed, which presents as "it worked for
    a while and then stopped". So repeats are recognised by their
    *timing* — anything arriving within REPEAT_GAP_S of the last one is a
    repeat, anything later is a genuine new press, and a stale hold is
    closed out first.

  * **All shortcuts must be declared, even unused ones.** A shortcut that
    was never bound cannot appear in the desktop's editor, so leaving one
    out means the user has no way to ever assign it.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import time

from gi.repository import GLib

from . import portal
from .hotkeys import NON_PRINTING_KEYS, parse_combo

log = logging.getLogger("talkin.hotkeys_portal")

IFACE = "org.freedesktop.portal.GlobalShortcuts"
SESSION_IFACE = "org.freedesktop.portal.Session"

# Talkin's modifier names -> the portal's shortcut syntax.
_MODIFIER_TRIGGERS = {"ctrl": "CTRL", "alt": "ALT", "shift": "SHIFT"}

# Talkin's trigger names -> X keysym names, which the portal expects for
# anything that is not a plain printable character.
_KEYSYM_NAMES = {
    "escape": "Escape", "tab": "Tab", "insert": "Insert", "delete": "Delete",
    "home": "Home", "end": "End", "page_up": "Page_Up",
    "page_down": "Page_Down", "up": "Up", "down": "Down", "left": "Left",
    "right": "Right", "pause": "Pause", "scroll_lock": "Scroll_Lock",
    "menu": "Menu", "space": "space",
}
_KEYSYM_NAMES.update({"f{}".format(n): "F{}".format(n) for n in range(1, 13)})

# Bare modifier keys: valid combos on X11, silently unbindable on Wayland.
BARE_MODIFIERS = {"ctrl_r", "alt_r", "shift_r", "ctrl_l", "alt_l", "shift_l"}

# Suggested defaults for the first bind only; the compositor's own value
# wins afterwards. Right Ctrl (Talkin's default) is a bare modifier and
# can never bind here.
WAYLAND_DEFAULT_HOLD = "ctrl+alt+space"
_FALLBACK_TRIGGERS = {
    "hold": "CTRL+ALT+space",
    "toggle": "CTRL+ALT+d",
    "correction": "CTRL+ALT+c",
}

# Auto-repeat arrives ~30ms apart, with the first repeat ~500ms after the
# initial press. Anything slower than this is a real new keypress, not a
# repeat — generous enough to never mistake a repeat for a press, short
# enough that recovering from a missed release costs one keystroke.
REPEAT_GAP_S = 1.0

# Releases genuinely go missing: the compositor sometimes never sends
# Deactivated, and the recording then runs until the user presses the key
# again. Since Activated repeats about 33 times a second while a key is
# held, a gap in that stream is itself evidence the key came up. Treat
# repeat silence longer than this as a release.
HEARTBEAT_GRACE_S = 0.8
_HEARTBEAT_POLL_MS = 250

# Only trust repeat silence once repeats have actually been seen for this
# press. A desktop with key repeat switched off would otherwise look
# permanently silent and cut every dictation short.
_MIN_REPEATS = 2

# A release arriving this soon after the press is not a finger lifting —
# nobody dictates in a third of a second. Observed in use: holds cut off
# at 0.0-0.9s produced no text at all, every time, while every hold over
# a second worked. So an early release is held back rather than acted on;
# if the key really was tapped, the hold simply ends at MIN_HOLD_S and
# nothing is lost, because a sub-second recording contains no speech
# worth keeping anyway.
MIN_HOLD_S = 0.7

# How many app ids to try before accepting that this desktop will not
# identify us. Bounded so a misconfigured system cannot spin.
_MAX_ID_ATTEMPTS = 4

_ACTIONS = ("hold", "toggle", "correction")
_CONFIG_KEYS = {
    "hold": "hotkey_hold",
    "toggle": "hotkey_toggle",
    "correction": "correction_hotkey",
}


def to_trigger(combo_text):
    """Talkin combo -> portal trigger string, or None if unbindable.

    "ctrl+alt+c" -> "CTRL+ALT+c";  "f9" -> "F9";  "ctrl_r" -> None.
    """
    mods, trigger = parse_combo(combo_text)
    if trigger is None:
        return None
    if trigger in BARE_MODIFIERS:
        return None  # silently dropped by the portal; refuse it up front
    key = _KEYSYM_NAMES.get(trigger)
    if key is None:
        if len(trigger) != 1:
            return None
        key = trigger
    parts = [_MODIFIER_TRIGGERS[m] for m in ("ctrl", "alt", "shift")
             if m in mods]
    parts.append(key)
    return "+".join(parts)


def combo_is_bindable(combo_text):
    """Whether this combo could work on Wayland at all."""
    _mods, trigger = parse_combo(combo_text)
    if trigger is None:
        return True  # unset is fine
    return to_trigger(combo_text) is not None


class PortalHotkeys:
    """Global shortcuts via the portal, with the same interface as before."""

    def __init__(self, config, on_hold_press, on_hold_release, on_toggle,
                 on_correction, on_problem=None):
        self.config = config
        self._on_problem = on_problem
        self._warned = set()
        self._callbacks = {
            "hold": (on_hold_press, on_hold_release),
            "toggle": (on_toggle, None),
            "correction": (on_correction, None),
        }
        self._session = None
        self._subscriptions = []
        self._held = {}           # shortcut id -> time of its last Activated
        self._repeats = {}        # shortcut id -> repeats seen this press
        self._watchdog = None
        self._pending_release = {}   # id -> timer id for a deferred stop
        self._bound = {}          # shortcut id -> human-readable trigger
        self._ready = False
        self._on_change = None    # Settings refreshes itself through this
        self._id_attempts = 0

        # No explicit registration here: portal claims the app id when the
        # bus connection is first opened, which must happen before any
        # other portal call. Registering again only logs a confusing
        # "connection already associated" warning.
        self._subscribe()
        self._create_session()

    # -- portal lifecycle ------------------------------------------------

    def _subscribe(self):
        for signal in ("Activated", "Deactivated"):
            self._subscriptions.append(
                portal.subscribe(IFACE, signal, self._on_signal))
        self._subscriptions.append(
            portal.subscribe(IFACE, "ShortcutsChanged", self._on_changed))
        # If the compositor drops our session (suspend, restart of the
        # portal, revoked permission) every shortcut goes dead silently.
        # Rebuild rather than sit there ignoring keys.
        self._subscriptions.append(
            portal.subscribe(SESSION_IFACE, "Closed", self._on_session_closed))

    def _create_session(self):
        def created(results):
            self._session = results.get("session_handle")
            log.info("global shortcuts session: %s", self._session)
            self._bind()

        def failed(exc):
            # Registering an app id can succeed and the session still be
            # refused with the same "an app id is required": the backend
            # re-resolves the id, and one we wrote a moment ago is not in
            # the desktop database yet. Walk down the remaining candidates
            # rather than giving up, since a wrong id here means no
            # hotkeys at all.
            if self._id_attempts < _MAX_ID_ATTEMPTS and \
                    "app id" in str(exc).lower():
                self._id_attempts += 1
                nxt = portal.register_next_app_id()
                if nxt:
                    log.info("retrying the shortcuts session as %r", nxt)
                    self._create_session()
                    return
            self._ready = True
            log.warning("could not create a shortcuts session: %s", exc)

        portal.call(IFACE, "CreateSession", "(a{sv})", ({
            "session_handle_token": GLib.Variant("s", "talkin_shortcuts"),
        },), created, failed)

    def _preferred(self, action):
        """The trigger to suggest for `action` on a first bind."""
        combo = (self.config.get(_CONFIG_KEYS[action]) or "").strip()
        trigger = to_trigger(combo) if combo else None
        if trigger is None and combo:
            log.info("%r cannot bind on Wayland; suggesting the default for %s",
                     combo, action)
        return trigger or _FALLBACK_TRIGGERS[action]

    def _bind(self):
        """Declare all three shortcuts.

        Every action is declared, including ones the user has not set: an
        undeclared shortcut cannot appear in the desktop's shortcut
        editor, so omitting it would make it permanently unassignable.
        """
        if self._session is None:
            return

        shortcuts = [
            (action, {
                "description": GLib.Variant("s", _describe(action)),
                "preferred_trigger": GLib.Variant("s", self._preferred(action)),
            })
            for action in _ACTIONS
        ]

        def bound(results):
            self._ready = True
            self._store(results.get("shortcuts", []) or [])
            for action in _ACTIONS:
                if action in self._bound:
                    log.info("bound %s -> %s", action, self._bound[action])
                else:
                    log.warning("the compositor did not bind %s", action)
                    self._warn(action)

        def failed(exc):
            self._ready = True
            log.warning("BindShortcuts failed: %s", exc)

        portal.call(IFACE, "BindShortcuts", "(oa(sa{sv})sa{sv})",
                    (self._session, shortcuts, "", {}), bound, failed)

    def _store(self, returned):
        self._bound = {
            sid: (opts.get("trigger_description") or "")
            for sid, opts in returned
        }
        if self._on_change is not None:
            self._on_change()

    # -- events ----------------------------------------------------------

    def _on_signal(self, _conn, _sender, _path, _iface, signal, params):
        try:
            session, shortcut_id, _timestamp, _options = params.unpack()
        except Exception:
            return
        if self._session is not None and session != self._session:
            return  # another app's session on the same bus

        pressed, released = self._callbacks.get(shortcut_id, (None, None))
        now = time.monotonic()

        if signal == "Activated":
            last = self._held.get(shortcut_id)
            if last is not None and (now - last) < REPEAT_GAP_S:
                # auto-repeat: refresh the heartbeat and count it
                self._held[shortcut_id] = now
                self._repeats[shortcut_id] = self._repeats.get(shortcut_id, 0) + 1
                return
            if last is not None:
                # A new press while still "held" means the release never
                # arrived. Close the old one out so the app is not left
                # mid-recording, then treat this as the fresh press it is.
                log.info("%s: missed release, recovering", shortcut_id)
                self._held.pop(shortcut_id, None)
                if released is not None:
                    released()
            # A press cancels any deferred stop: the key is clearly still
            # in use, so the early release really was spurious.
            self._cancel_pending(shortcut_id)
            self._held[shortcut_id] = now
            self._repeats[shortcut_id] = 0
            self._arm_watchdog()
            if pressed is not None:
                pressed()
        else:
            started = self._held.get(shortcut_id)
            if started is None:
                return
            held_for = now - started
            if held_for < MIN_HOLD_S and shortcut_id not in self._pending_release:
                # Too soon to be real. Wait out the rest of the minimum
                # hold; if no further press arrives, stop then.
                delay = int((MIN_HOLD_S - held_for) * 1000)
                log.info("%s: release after only %dms; holding on",
                         shortcut_id, int(held_for * 1000))
                self._pending_release[shortcut_id] = GLib.timeout_add(
                    delay, self._finish_release, shortcut_id)
                return
            self._finish_release(shortcut_id)

    def _cancel_pending(self, shortcut_id):
        timer = self._pending_release.pop(shortcut_id, None)
        if timer is not None:
            GLib.source_remove(timer)

    def _finish_release(self, shortcut_id):
        self._pending_release.pop(shortcut_id, None)
        if self._held.pop(shortcut_id, None) is None:
            return False
        self._repeats.pop(shortcut_id, None)
        _pressed, released = self._callbacks.get(shortcut_id, (None, None))
        if released is not None:
            released()
        return False   # one shot when called from a timer

    def _arm_watchdog(self):
        if self._watchdog is None:
            self._watchdog = GLib.timeout_add(_HEARTBEAT_POLL_MS,
                                              self._check_still_held)

    def _check_still_held(self):
        """End a hold whose repeats have stopped but whose release never came."""
        now = time.monotonic()
        for shortcut_id in list(self._held):
            if self._repeats.get(shortcut_id, 0) < _MIN_REPEATS:
                continue  # no repeats on this desktop; cannot infer anything
            if (now - self._held[shortcut_id]) <= HEARTBEAT_GRACE_S:
                continue
            log.info("%s: repeats stopped and no release arrived; "
                     "treating the key as up", shortcut_id)
            self._cancel_pending(shortcut_id)
            self._held.pop(shortcut_id, None)
            self._repeats.pop(shortcut_id, None)
            _pressed, released = self._callbacks.get(shortcut_id, (None, None))
            if released is not None:
                released()
        if not self._held:
            self._watchdog = None
            return False   # nothing held; stop polling
        return True

    def _on_changed(self, _conn, _sender, _path, _iface, _signal, params):
        """The user rebound something in the desktop's own editor."""
        try:
            session, shortcuts = params.unpack()
        except Exception:
            return
        if self._session is not None and session != self._session:
            return
        self._store(shortcuts)
        log.info("shortcuts changed by the desktop: %s", self._bound)

    def _on_session_closed(self, _conn, _sender, path, _iface, _signal, _params):
        if self._session is None or path != self._session:
            return
        log.warning("shortcuts session closed by the compositor; rebuilding")
        self._session = None
        self._ready = False
        self._held.clear()
        self._repeats.clear()
        GLib.timeout_add_seconds(1, self._reopen)

    def _reopen(self):
        if self._session is None:
            self._create_session()
        return False  # one shot

    # -- interface used by the app and Settings --------------------------

    @staticmethod
    def can_configure():
        """Whether this desktop can open its own shortcut editor for us.

        ConfigureShortcuts arrived in GlobalShortcuts version 2. On
        version 1 the method is still listed on the interface but answers
        UnknownMethod, so the version is the only honest check — and
        offering a button that always fails is worse than not offering
        one.
        """
        return portal.interface_version(IFACE) >= 2

    def configure(self, parent_window=""):
        """Open the desktop's own shortcut editor for our shortcuts."""
        if self._session is None or not self.can_configure():
            log.info("this desktop cannot open a shortcut editor for us")
            return False
        try:
            portal.call_plain(IFACE, "ConfigureShortcuts", "(osa{sv})",
                              (self._session, parent_window, {}))
            return True
        except Exception as exc:
            log.warning("could not open the shortcut editor: %s", exc)
            return False

    def set_on_change(self, callback):
        """Called whenever the bound shortcuts change (Settings listens)."""
        self._on_change = callback

    def reload(self):
        """Settings changed. The compositor owns the bindings, so there is
        nothing to re-declare — a rebind here would discard the user's own
        choice and force our defaults back on them."""
        return

    def stop(self):
        if self._watchdog is not None:
            GLib.source_remove(self._watchdog)
            self._watchdog = None
        for shortcut_id in list(self._pending_release):
            self._cancel_pending(shortcut_id)
        for subscription in self._subscriptions:
            portal.unsubscribe(subscription)
        self._subscriptions = []
        portal.close_session(self._session)
        self._session = None

    def _warn(self, action):
        if action in self._warned or self._on_problem is None:
            return
        self._warned.add(action)
        self._on_problem(action, "")

    def bound_triggers(self):
        """{action: 'Ctrl+Alt+Space'} exactly as the compositor describes it."""
        return dict(self._bound)

    @property
    def ready(self):
        return self._ready


def _describe(action):
    # Shown by the compositor's own shortcut editor, which has no access
    # to Talkin's locale and may display it long after Talkin has quit.
    return {
        "hold": "Hold to dictate",
        "toggle": "Start or stop dictation",
        "correction": "Teach Talkin a word",
    }.get(action, action)
