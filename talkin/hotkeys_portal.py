"""Global hotkeys through the GlobalShortcuts portal (Wayland).

The X11 path in hotkeys.py watches the whole key stream and grabs keys at
the server. Neither is possible under Wayland: the compositor only
delivers keys to the focused surface, so an observer sees nothing when
another app has focus — which is exactly when dictation is wanted. The
portal is the sanctioned replacement: we describe the shortcuts we want,
the compositor owns the binding, and it signals us when one fires.

Three behaviours here were established by spiking against a live GNOME 50
session, and none of them are in the spec:

  * The portal REPEATS Activated while a combo is held — once, then again
    after ~500ms, then ~33 times a second — and sends exactly one
    Deactivated on release. Taken naively that restarts the recording tens
    of times a second, the same failure the X11 backend hit in v1.0.34.
    Here the release is unambiguous, so the cure is simply to make
    Activated idempotent rather than to debounce anything.

  * Bare modifiers cannot be bound. A trigger of "CTRL_R" is dropped from
    the result silently — no error, just missing. Talkin's default hold
    key is Right Ctrl, so on Wayland that default cannot work and the user
    has to be told rather than left with a dead key.

  * The portal refuses callers with no app id, so portal.register_app_id()
    must run first (see portal.py).
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging

from gi.repository import GLib

from . import portal
from .hotkeys import NON_PRINTING_KEYS, parse_combo

log = logging.getLogger("talkin.hotkeys_portal")

IFACE = "org.freedesktop.portal.GlobalShortcuts"

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

# Bare modifier keys: valid Talkin combos on X11, unbindable on Wayland.
BARE_MODIFIERS = {"ctrl_r", "alt_r", "shift_r", "ctrl_l", "alt_l", "shift_l"}

# Talkin's shipped default hold key is `ctrl_r`, a bare modifier, so on
# Wayland it can never bind. This is the replacement used when migrating
# such a default: a real combination, comfortable to hold, and unlikely to
# collide with an existing desktop shortcut.
WAYLAND_DEFAULT_HOLD = "ctrl+alt+space"

# Portal shortcut ids, and which callback each drives.
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
    """Whether this combo can work on Wayland at all (for Settings)."""
    _mods, trigger = parse_combo(combo_text)
    if trigger is None:
        return True  # unset is fine
    return to_trigger(combo_text) is not None


class PortalHotkeys:
    """Drop-in replacement for Hotkeys, backed by the portal.

    Same constructor and same callbacks, so app.py does not care which
    backend it got. Callbacks are already on the GLib main loop because
    D-Bus signals are delivered there.
    """

    def __init__(self, config, on_hold_press, on_hold_release, on_toggle,
                 on_correction, on_problem=None):
        self.config = config
        # Told about combos the compositor will not bind. Without this the
        # app looks fine and simply never responds to the hold key, which
        # is the worst possible failure: silent.
        self._on_problem = on_problem
        self._warned = set()
        self._callbacks = {
            "hold": (on_hold_press, on_hold_release),
            "toggle": (on_toggle, None),
            "correction": (on_correction, None),
        }
        self._session = None
        self._subscriptions = []
        self._active = set()      # shortcut ids currently held down
        self._bound = {}          # id -> human-readable trigger
        self._rejected = {}       # id -> the combo we could not bind
        self._pending_rebind = False
        self._ready = False

        portal.register_app_id()
        self._subscribe()
        self._create_session()

    # -- portal lifecycle ------------------------------------------------

    def _subscribe(self):
        for signal in ("Activated", "Deactivated"):
            self._subscriptions.append(
                portal.subscribe(IFACE, signal, self._on_signal))

    def _create_session(self):
        def created(results):
            self._session = results.get("session_handle")
            log.info("global shortcuts session: %s", self._session)
            self._bind()

        def failed(exc):
            log.warning("could not create a shortcuts session: %s", exc)

        portal.call(IFACE, "CreateSession", "(a{sv})", ({
            "session_handle_token": GLib.Variant("s", "talkin_shortcuts"),
        },), created, failed)

    def _wanted(self):
        """[(shortcut_id, combo_text, trigger)] for every set, bindable combo."""
        wanted = []
        self._rejected = {}
        for action in _ACTIONS:
            combo = self.config.get(_CONFIG_KEYS[action]) or ""
            if not combo.strip():
                continue
            trigger = to_trigger(combo)
            if trigger is None:
                self._rejected[action] = combo
                log.warning(
                    "%r cannot be bound on Wayland (bare modifiers and "
                    "unknown keys are unsupported); %s hotkey inactive",
                    combo, action)
                self._warn(action)
                continue
            wanted.append((action, combo, trigger))
        return wanted

    def _bind(self):
        if self._session is None:
            return
        wanted = self._wanted()
        if not wanted:
            self._ready = True
            return

        shortcuts = [
            (action, {
                "description": GLib.Variant("s", _describe(action)),
                "preferred_trigger": GLib.Variant("s", trigger),
            })
            for action, _combo, trigger in wanted
        ]

        def bound(results):
            self._ready = True
            returned = results.get("shortcuts", []) or []
            self._bound = {
                sid: opts.get("trigger_description", "")
                for sid, opts in returned
            }
            # The portal drops what it cannot bind instead of erroring, so
            # compare against what we asked for rather than trusting code=0.
            for action, combo, trigger in wanted:
                if action not in self._bound:
                    self._rejected[action] = combo
                    log.warning("the compositor did not bind %s (%s)",
                                action, trigger)
                    self._warn(action)
                else:
                    log.info("bound %s -> %s", action, self._bound[action])
            if self._pending_rebind:
                self._pending_rebind = False
                self._rebind_session()

        def failed(exc):
            self._ready = True
            log.warning("BindShortcuts failed: %s", exc)

        portal.call(IFACE, "BindShortcuts", "(oa(sa{sv})sa{sv})",
                    (self._session, shortcuts, "", {}), bound, failed)

    # -- events ----------------------------------------------------------

    def _on_signal(self, _conn, _sender, _path, _iface, signal, params):
        try:
            session, shortcut_id, _timestamp, _options = params.unpack()
        except Exception:
            return
        if self._session is not None and session != self._session:
            return  # another app's session on the same bus

        pressed, released = self._callbacks.get(shortcut_id, (None, None))
        if signal == "Activated":
            # Auto-repeat: the portal re-Activates ~33x/second while held.
            # Only the first one is a real press.
            if shortcut_id in self._active:
                return
            self._active.add(shortcut_id)
            if pressed is not None:
                pressed()
        else:
            if shortcut_id not in self._active:
                return
            self._active.discard(shortcut_id)
            if released is not None:
                released()

    # -- interface shared with the X11 backend ---------------------------

    def reload(self):
        """Re-read combos after a Settings change.

        A session's shortcuts cannot be re-declared, so this replaces the
        session. If the previous bind is still in flight we defer, since
        two overlapping binds race and the loser's shortcuts stay dead.
        """
        if not self._ready:
            self._pending_rebind = True
            return
        self._rebind_session()

    def _rebind_session(self):
        self._ready = False
        self._active.clear()
        self._bound = {}
        self._close_session()
        self._create_session()

    def _close_session(self):
        portal.close_session(self._session)
        self._session = None

    def stop(self):
        for subscription in self._subscriptions:
            portal.unsubscribe(subscription)
        self._subscriptions = []
        self._close_session()

    # -- for the Settings window -----------------------------------------

    def _warn(self, action):
        """Tell the user once per action, not once per rebind."""
        if action in self._warned or self._on_problem is None:
            return
        self._warned.add(action)
        self._on_problem(action, self._rejected.get(action, ""))

    def bound_triggers(self):
        """{action: 'Press <Control><Alt>z'} as the compositor describes it."""
        return dict(self._bound)

    def rejected(self):
        """{action: combo} for combos this session could not bind."""
        return dict(self._rejected)


def _describe(action):
    # Deliberately plain English rather than a translated string: this text
    # is shown by the compositor's own shortcut UI, which has no access to
    # Talkin's locale and may display it long after Talkin has exited.
    return {
        "hold": "Hold to dictate",
        "toggle": "Start or stop dictation",
        "correction": "Teach Talkin a word",
    }.get(action, action)
