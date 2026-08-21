"""System tray icon: left-click toggles dictation, animated while
listening.

Primary backend is Gtk.StatusIcon, which delivers real click events -
left-click starts/stops a dictation, right-click opens the menu - and
takes its image as an in-process pixbuf, so the icon can be redrawn
every animation frame (a live waveform while listening, a revolving
arc while transcribing; the mid-screen overlay circle used to carry
those and is gone). Frames are drawn with cairo directly - no SVG
loading in-process, so no dependence on the librsvg gdk-pixbuf loader
that isn't reliably present inside the AppImage.

AppIndicator remains as an automatic fallback for desktops that never
embed legacy status icons (stock GNOME): same menu, static SVG icons,
no left-click - degraded but functional. The fallback triggers only
when the status icon reports it isn't embedded after a grace period.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import math
import os
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib

from .config import ASSET_DIR
from .i18n import t

log = logging.getLogger("talkin.tray")

_FPS_MS = 100          # animation frame interval while listening/thinking
_EMBED_GRACE_S = 4     # how long to wait before falling back to AppIndicator

_NAVY = (0x1c / 255, 0x1e / 255, 0x23 / 255)
_YELLOW = (0xfb / 255, 0xc7 / 255, 0x11 / 255)
_WHITE = (1.0, 1.0, 1.0)

# Waveform bar geometry from the reference SVGs, in a 24-unit viewbox:
# x positions and the idle/listening half-heights of each bar.
_BAR_X = (6.5, 9.25, 12.0, 14.75, 17.5)
_IDLE_HALF = (1.5, 3.0, 4.5, 3.0, 1.5)
_LISTEN_HALF = (2.5, 4.5, 6.0, 4.0, 2.0)

_ANIMATED = {"listening", "thinking", "loading", "downloading"}

# The AppIndicator fallback's static files (also used for the .desktop
# icon); the StatusIcon path never touches them.
_SVG_ICONS = {
    "loading": "talkin-thinking",
    "downloading": "talkin-thinking",
    "idle": "talkin-idle",
    "listening": "talkin-listening",
    "thinking": "talkin-thinking",
    "paused": "talkin-paused",
}


def _draw_frame(size, state, phase, level):
    """One tray frame as a pixbuf: the SVG design, animated."""
    import cairo
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    s = size / 24.0
    cx = cy = size / 2.0

    cr.set_source_rgb(*_NAVY)
    cr.arc(cx, cy, 10.5 * s, 0, 2 * math.pi)
    cr.fill()

    if state == "listening":
        cr.set_source_rgba(*_YELLOW, 0.9)
        cr.set_line_width(1.4 * s)
    else:
        cr.set_source_rgba(*_WHITE, 0.18)
        cr.set_line_width(1.2 * s)
    cr.arc(cx, cy, 10.0 * s, 0, 2 * math.pi)
    cr.stroke()

    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    if state == "paused":
        cr.set_source_rgba(*_YELLOW, 0.55)
        cr.set_line_width(2.6 * s)
        for x in (9.5, 14.5):
            cr.move_to(x * s, 8 * s)
            cr.line_to(x * s, 16 * s)
            cr.stroke()
    elif state in ("thinking", "loading", "downloading"):
        cr.set_source_rgba(*_YELLOW, 1.0)
        cr.set_line_width(2.2 * s)
        start = phase * 1.6
        cr.arc(cx, cy, 8.5 * s, start, start + math.pi * 1.4)
        cr.stroke()
        cr.arc(cx, cy, 2.2 * s, 0, 2 * math.pi)
        cr.fill()
    elif state == "listening":
        cr.set_source_rgba(*_YELLOW, 1.0)
        cr.set_line_width(1.8 * s)
        for i, x in enumerate(_BAR_X):
            full = _LISTEN_HALF[i]
            wobble = 0.55 + 0.45 * abs(math.sin(phase + i * 0.9))
            half = full * min(1.0, wobble + level * 0.6)
            half = max(half, 1.2)
            cr.move_to(x * s, (12 - half) * s)
            cr.line_to(x * s, (12 + half) * s)
            cr.stroke()
    else:  # idle
        cr.set_source_rgba(*_YELLOW, 0.75)
        cr.set_line_width(1.8 * s)
        for i, x in enumerate(_BAR_X):
            half = _IDLE_HALF[i]
            cr.move_to(x * s, (12 - half) * s)
            cr.line_to(x * s, (12 + half) * s)
            cr.stroke()

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


class Tray:
    """on_activate fires on a left-click of the icon (the caller wires
    it to start/stop dictation); the other callbacks come from the
    right-click menu exactly as before."""

    def __init__(self, on_settings, on_toggle_pause, on_restart, on_quit,
                 on_activate=None):
        self.on_toggle_pause = on_toggle_pause
        self.on_activate = on_activate
        self._state = "loading"
        self._phase = 0.0
        self._level = 0.0
        self._level_lock = threading.Lock()
        self._timer = None
        self._size = 24
        self._indicator = None

        self._menu = self._build_menu(
            on_settings, on_toggle_pause, on_restart, on_quit)

        self._icon = Gtk.StatusIcon()
        self._icon.set_title("Lightmorphic Talkin")
        self._icon.connect("activate", self._on_left_click)
        self._icon.connect("popup-menu", self._on_right_click)
        self._icon.connect("size-changed", self._on_size_changed)
        self._render()
        GLib.timeout_add_seconds(_EMBED_GRACE_S, self._check_embedded)

    # -- public API --------------------------------------------------

    def set_state(self, state):
        self._state = state
        if self._indicator is not None:
            self._set_indicator_state(state)
        else:
            self._icon.set_tooltip_text(
                "Lightmorphic Talkin — " + t("tray.status." + state))
            self._render()
        self._status_item.set_label(t("tray.status." + state))
        self._pause_item.set_label(
            t("tray.resume") if state == "paused" else t("tray.pause"))
        self._sync_timer()

    def set_level(self, level):
        """Live mic level from the audio thread; drives the waveform."""
        with self._level_lock:
            self._level = level

    # -- backends ----------------------------------------------------

    def _build_menu(self, on_settings, on_toggle_pause, on_restart, on_quit):
        menu = Gtk.Menu()
        self._status_item = Gtk.MenuItem(label=t("tray.status.loading"))
        self._status_item.set_sensitive(False)
        menu.append(self._status_item)
        menu.append(Gtk.SeparatorMenuItem())
        settings_item = Gtk.MenuItem(label=t("tray.open_settings"))
        settings_item.connect("activate", lambda *_: on_settings())
        menu.append(settings_item)
        self._pause_item = Gtk.MenuItem(label=t("tray.pause"))
        self._pause_item.connect("activate", lambda *_: on_toggle_pause())
        menu.append(self._pause_item)
        restart_item = Gtk.MenuItem(label=t("tray.restart"))
        restart_item.connect("activate", lambda *_: on_restart())
        menu.append(restart_item)
        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label=t("tray.quit"))
        quit_item.connect("activate", lambda *_: on_quit())
        menu.append(quit_item)
        menu.show_all()
        return menu

    def _on_left_click(self, _icon):
        if self.on_activate is not None:
            self.on_activate()

    def _on_right_click(self, _icon, button, activate_time):
        self._menu.popup(None, None, Gtk.StatusIcon.position_menu,
                         self._icon, button, activate_time)

    def _on_size_changed(self, _icon, size):
        self._size = max(16, size)
        self._render()
        return True

    def _check_embedded(self):
        if self._icon.is_embedded():
            log.info("tray: status icon embedded (left-click enabled)")
            return False
        log.info("tray: status icon never embedded; "
                 "falling back to AppIndicator (menu only)")
        self._icon.set_visible(False)
        # Importing AppIndicator is NOT proof that it works. Only the
        # typelib ships with the bundle; the matching shared library is
        # always the host's, and a GI typelib dlopen()s that library
        # lazily — on the first real call, not at import. So a machine
        # without libayatana-appindicator3 imports cleanly and then throws
        # GError here. Everything through the first call must be guarded,
        # or the tray dies with a traceback and Talkin runs invisibly.
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as AppIndicator
            indicator = AppIndicator.Indicator.new(
                "talkin", "talkin-idle",
                AppIndicator.IndicatorCategory.APPLICATION_STATUS)
            indicator.set_icon_theme_path(ASSET_DIR)
            indicator.set_title("Lightmorphic Talkin")
            indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            indicator.set_menu(self._menu)
        except (ImportError, ValueError, GLib.GError, TypeError, AttributeError):
            log.warning(
                "tray unavailable: this desktop does not embed status icons "
                "and libayatana-appindicator3 is missing. Talkin still works "
                "from its hotkeys; install libayatana-appindicator3 (and, on "
                "GNOME, the AppIndicator extension) to get the tray icon back.")
            self._indicator = None
            return False

        self._indicator = indicator
        self._set_indicator_state(self._state)
        return False

    def _set_indicator_state(self, state):
        icon = _SVG_ICONS.get(state, "talkin-idle")
        icon_path = os.path.join(ASSET_DIR, icon + ".svg")
        self._indicator.set_icon_full(
            icon_path, "Lightmorphic Talkin — " + t("tray.status." + state))

    # -- animation ---------------------------------------------------

    def _sync_timer(self):
        # The indicator fallback can't animate (its icon is a file
        # path, not a pixbuf) - static icons only there.
        want = self._state in _ANIMATED and self._indicator is None
        if want and self._timer is None:
            self._timer = GLib.timeout_add(_FPS_MS, self._tick)
        elif not want and self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None
            self._render()

    def _tick(self):
        self._phase += 0.55
        self._render()
        return True

    def _render(self):
        with self._level_lock:
            level = self._level
        self._icon.set_from_pixbuf(
            _draw_frame(self._size, self._state, self._phase, level))
