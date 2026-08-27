"""Which display server and desktop we are running under.

Talkin ships one binary for every Linux desktop, so nothing may assume
X11. This module is the single place that answers "what are we on?", and
everything else picks a backend from its answer rather than sniffing the
environment itself.

Detection is deliberately environment-based rather than "can I open an X
display?": under Wayland XWayland is usually running too, so an X display
opens fine and would fool us into taking the X11 path on a Wayland
session — where key grabs and XTEST injection silently do nothing.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import os

log = logging.getLogger("talkin.session")

WAYLAND = "wayland"
X11 = "x11"
UNKNOWN = "unknown"


def display_server():
    """WAYLAND, X11 or UNKNOWN for the current session."""
    kind = (os.environ.get("XDG_SESSION_TYPE") or "").strip().lower()
    if kind in (WAYLAND, X11):
        return kind
    # XDG_SESSION_TYPE is unset in some minimal or nested sessions, so
    # fall back to the sockets themselves. WAYLAND_DISPLAY is checked
    # first because a Wayland session usually also has DISPLAY set for
    # XWayland, but never the other way round.
    if os.environ.get("WAYLAND_DISPLAY"):
        return WAYLAND
    if os.environ.get("DISPLAY"):
        return X11
    return UNKNOWN


def is_wayland():
    return display_server() == WAYLAND


def desktop():
    """Lower-case desktop name: 'gnome', 'kde', 'x-cinnamon', ... or ''."""
    raw = (os.environ.get("XDG_CURRENT_DESKTOP")
           or os.environ.get("XDG_SESSION_DESKTOP") or "")
    # The spec allows a colon-separated list ("ubuntu:GNOME").
    return raw.split(":")[0].strip().lower()


def describe():
    """One line for the log, so bug reports say what we detected."""
    return "{} on {}".format(desktop() or "unknown desktop", display_server())
