"""Hotkey combos: parsing, validation, and choosing the backend.

Keyboard shortcuts are switched off by default and have no page in
Settings. On Wayland the compositor's press and release signals arrived
both too late and too early, and every dictation that went wrong in use
went wrong because of a key while none went wrong because of a click, so
the floating button and the tray icon are the whole story now.

What remains here is the shared vocabulary — how a combo is written down
and what makes one valid — and the portal backend behind it, kept
working for anyone who turns the flag back on by hand.

A combo is a canonical string like "alt+z", "ctrl+alt+c", or a bare
special key like "f9". Modifiers are always spelled ctrl/alt/shift and
come before the trigger key.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging

log = logging.getLogger("talkin.hotkeys")

MODIFIER_NAMES = ("ctrl", "alt", "shift")

# Keys that don't type anything by themselves, so they're safe to use
# as a hotkey trigger with no modifier at all. Note that a compositor
# will still refuse the bare-modifier entries here (see
# hotkeys_portal.BARE_MODIFIERS) — "safe" and "bindable" are different
# questions, and both are checked before a combo is accepted.
NON_PRINTING_KEYS = {
    "ctrl_r", "alt_r", "shift_r", "f1", "f2", "f3", "f4", "f5", "f6",
    "f7", "f8", "f9", "f10", "f11", "f12", "pause", "scroll_lock",
    "menu", "insert", "delete", "home", "end", "page_up", "page_down",
    "up", "down", "left", "right", "tab", "escape",
}


class HotkeysUnavailable(RuntimeError):
    """No way to register a global shortcut on this desktop."""


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


def create_hotkeys(config, on_hold_press, on_hold_release, on_toggle,
                   on_correction, on_problem=None):
    """Build the hotkey backend.

    There is exactly one: the GlobalShortcuts portal. A Wayland
    compositor only delivers keys to the focused surface, so an app
    cannot watch the key stream itself — which is the whole reason the
    portal exists.

    Raises HotkeysUnavailable when the portal cannot be reached: without
    it Talkin has no way to learn that a key was pressed, and starting
    anyway would present as an app that simply ignores its own hotkey.
    """
    # Imported here rather than at module scope: hotkeys_portal imports
    # names from this module, so a top-level import would be circular.
    from . import portal, session
    from .hotkeys_portal import PortalHotkeys

    if not session.is_wayland():
        log.warning("global shortcuts need the portal, and this is not a "
                    "Wayland session (%s)", session.describe())

    if not portal.available():
        raise HotkeysUnavailable("no desktop portal on the session bus")
    if not portal.has_interface("org.freedesktop.portal.GlobalShortcuts"):
        raise HotkeysUnavailable(
            "this desktop's portal does not provide GlobalShortcuts")

    log.info("hotkey backend: GlobalShortcuts portal (%s)",
             session.describe())
    return PortalHotkeys(config, on_hold_press, on_hold_release,
                         on_toggle, on_correction, on_problem=on_problem)
