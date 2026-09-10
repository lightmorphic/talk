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

AppIndicator is used instead on GNOME, and as an automatic fallback
anywhere else the status icon never gets embedded: same menu, static
SVG icons, no left-click - degraded but functional.

GNOME is decided up front rather than by asking the status icon,
because on Ubuntu the answer is a lie. Something in the session claims
the X11 tray-manager selection without ever drawing anything, so
is_embedded() reports True, the fallback never runs, and the icon goes
nowhere - no tray icon and, since Settings lived only on its menu, no
way into Settings at all. GNOME has not embedded legacy status icons
since 3.26, so there is nothing to test for there anyway.
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

log = logging.getLogger("talk.tray")

_FPS_MS = 100          # animation frame interval while listening/thinking
# How much of the panel's icon box the design fills. The disc touches the
# edges at 1.14; the rest is asked for on top of the box.
_TRAY_FILL = 1.14

# ...and how much larger a pixbuf than the panel asked for. A panel is
# free to scale this back down to the box it offered, in which case
# nothing changes and nothing breaks; some do honour it, and on those the
# icon comes out the extra fifth larger. It is the only lever an
# application has here — the size of a tray icon is the panel's decision,
# not ours.
_TRAY_OVERSIZE = 1.2

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
    "loading": "talk-thinking",
    "downloading": "talk-thinking",
    "idle": "talk-idle",
    "listening": "talk-listening",
    "thinking": "talk-thinking",
    "paused": "talk-paused",
}


def _draw_frame(size, state, phase, level, progress=0.0, fill=1.0,
                bold_ring=False, badge=False):
    """One frame as a pixbuf: the SVG design, animated.

    `fill` scales the whole design about the centre. The design leaves a
    margin inside its box, which is right for the floating button — it is
    a window of its own with nothing around it — but wrong in a panel,
    where the icon is already small and that margin is wasted space. A
    fill of 1.14 puts the disc against the edges of the box, which is as
    large as it can be drawn without the sides being cut off.

    `bold_ring` brightens and thickens the outline. At panel size the
    faint ring the button uses all but vanished against a dark panel.
    """
    import cairo
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    s = size / 24.0 * fill
    # Work in the original 24-unit design space, re-centred, so every
    # coordinate below is untouched by the scaling.
    cr.translate(size / 2.0 - 12 * s, size / 2.0 - 12 * s)
    cx = cy = 12 * s

    cr.set_source_rgb(*_NAVY)
    cr.arc(cx, cy, 10.5 * s, 0, 2 * math.pi)
    cr.fill()

    if state == "listening":
        cr.set_source_rgba(*_YELLOW, 0.9)
        cr.set_line_width((1.9 if bold_ring else 1.4) * s)
    elif bold_ring:
        cr.set_source_rgba(*_WHITE, 0.42)
        cr.set_line_width(1.9 * s)
    else:
        cr.set_source_rgba(*_WHITE, 0.18)
        cr.set_line_width(1.2 * s)
    cr.arc(cx, cy, 10.0 * s, 0, 2 * math.pi)
    cr.stroke()

    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    if state == "paused":
        # Grey, not yellow: paused sat next to idle as two similar sets of
        # yellow bars, and a waveform of vertical lines reads as a pause
        # symbol at 22 pixels. Colour is what separates them at a glance.
        cr.set_source_rgba(*_WHITE, 0.45)
        cr.set_line_width(2.6 * s)
        for x in (9.5, 14.5):
            cr.move_to(x * s, 8 * s)
            cr.line_to(x * s, 16 * s)
            cr.stroke()
    elif state == "downloading":
        # A filling ring, not a spinner. A spinner conveys "busy" and
        # nothing else; during a 464 MB download the one thing worth
        # knowing is how far along it is, and whether it is moving at
        # all. The arc grows clockwise from the top until it closes.
        cr.set_source_rgba(*_YELLOW, 0.22)
        cr.set_line_width(2.2 * s)
        cr.arc(cx, cy, 8.5 * s, 0, 2 * math.pi)
        cr.stroke()
        if progress > 0:
            cr.set_source_rgba(*_YELLOW, 1.0)
            cr.set_line_width(2.2 * s)
            top = -math.pi / 2
            cr.arc(cx, cy, 8.5 * s, top, top + 2 * math.pi * min(1.0, progress))
            cr.stroke()
        cr.set_source_rgba(*_YELLOW, 1.0)
        cr.arc(cx, cy, 2.2 * s, 0, 2 * math.pi)
        cr.fill()
    elif state in ("thinking", "loading"):
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
    else:  # idle — drawn as something you can click, not something inert
        cr.set_source_rgba(*_YELLOW, 0.9)
        cr.set_line_width(1.8 * s)
        for i, x in enumerate(_BAR_X):
            half = _IDLE_HALF[i]
            cr.move_to(x * s, (12 - half) * s)
            cr.line_to(x * s, (12 + half) * s)
            cr.stroke()

    if badge:
        # A dot in the corner, the way every application says "there is
        # something here for you" without saying what. Blue, matching
        # the update dot's own "downloaded, your move" colour, and
        # drawn last so nothing else covers it.
        cr.set_source_rgb(0x22 / 255, 0x95 / 255, 0xF1 / 255)
        cr.arc(18.0 * s, 6.0 * s, 4.2 * s, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.45)
        cr.set_line_width(1.0 * s)
        cr.arc(18.0 * s, 6.0 * s, 4.2 * s, 0, 2 * math.pi)
        cr.stroke()

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _is_gnome():
    return "gnome" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


class Tray:
    """on_activate fires on a left-click of the icon (the caller wires
    it to start/stop dictation); the other callbacks come from the
    right-click menu exactly as before.

    The menu is also handed out through popup_at_pointer, so the
    floating button can raise the same one."""

    def __init__(self, on_settings, on_toggle_pause, on_restart, on_quit,
                 on_activate=None, on_show_button=None, on_teach=None,
                 dictionary_enabled=True):
        self.on_toggle_pause = on_toggle_pause
        self.on_activate = on_activate
        self.on_show_button = on_show_button
        self.on_teach = on_teach
        self._dictionary_enabled = dictionary_enabled
        self._state = "loading"
        self._phase = 0.0
        self._level = 0.0
        self._progress = 0.0   # 0..1 while downloading; fills the ring
        # A newer speech model exists and has not been fetched. Shown as
        # a corner dot, because Settings is usually closed when the
        # check comes back and a 464 MB download should not be
        # announced only where nobody is looking.
        self._model_update = False
        self._level_lock = threading.Lock()
        self._timer = None
        self._size = 24
        self._indicator = None

        # Kept so the menu can be rebuilt when the language changes.
        self._menu_callbacks = (on_settings, on_toggle_pause, on_restart,
                                on_quit)
        self._menu = self._build_menu(*self._menu_callbacks)

        self._icon = Gtk.StatusIcon()
        self._icon.set_title("Lightmorphic Talk")
        self._icon.connect("activate", self._on_left_click)
        self._icon.connect("popup-menu", self._on_right_click)
        self._icon.connect("size-changed", self._on_size_changed)
        self._render()
        if _is_gnome() and self._start_indicator():
            self._icon.set_visible(False)
        else:
            GLib.timeout_add_seconds(_EMBED_GRACE_S, self._check_embedded)

    # -- public API --------------------------------------------------

    def set_state(self, state):
        self._state = state
        if self._indicator is not None:
            self._set_indicator_state(state)
        else:
            self._icon.set_tooltip_text(
                "Lightmorphic Talk — " + t("tray.status." + state))
            self._render()
        self._status_item.set_label(t("tray.status." + state))
        self._pause_item.set_label(
            t("tray.resume") if state == "paused" else t("tray.pause"))
        self._sync_timer()

    def retranslate(self):
        """Rebuild the menu in the current language.

        Every label was set when the menu was built, so switching
        language did nothing visible until the app was restarted — which
        reads as a setting that does not work.
        """
        self._menu = self._build_menu(*self._menu_callbacks)
        if self._indicator is not None:
            self._indicator.set_menu(self._menu)
        self.set_state(self._state)

    def set_model_update(self, available):
        if self._model_update == bool(available):
            return
        self._model_update = bool(available)
        self._render()

    def set_progress(self, fraction):
        """How full the download ring should be (0..1).

        Also written into the tooltip: a ring shows roughly how far along
        it is, but "how long left?" needs a number, and hovering is where
        people look for one.
        """
        self._progress = max(0.0, min(1.0, fraction))
        if self._state == "downloading" and self._indicator is None:
            self._icon.set_tooltip_text("Lightmorphic Talk — {} {}%".format(
                t("tray.status.downloading"), int(self._progress * 100)))

    def set_level(self, level):
        """Live mic level from the audio thread; drives the waveform."""
        with self._level_lock:
            self._level = level

    # -- backends ----------------------------------------------------

    def hide(self):
        """Take the icon off the panel now, before anything else.

        Quit has to unload the speech model, and that is not instant. An
        icon still sitting there afterwards looks like an app that
        ignored being asked to close.
        """
        try:
            if self._indicator is not None:
                from gi.repository import AyatanaAppIndicator3 as AppIndicator
                self._indicator.set_status(AppIndicator.IndicatorStatus.PASSIVE)
            else:
                self._icon.set_visible(False)
        except Exception:
            log.debug("could not hide the tray icon", exc_info=True)

    def _build_menu(self, on_settings, on_toggle_pause, on_restart, on_quit):
        menu = Gtk.Menu()
        self._status_item = Gtk.MenuItem(label=t("tray.status.loading"))
        self._status_item.set_sensitive(False)
        menu.append(self._status_item)
        menu.append(Gtk.SeparatorMenuItem())
        # Teaching a word used to be the floating button's right-click
        # on its own. That click now raises this menu, so the action it
        # replaced has to be on it.
        self._teach_item = None
        if self.on_teach is not None:
            self._teach_item = Gtk.MenuItem(label=t("float.teach"))
            self._teach_item.connect("activate", lambda *_: self.on_teach())
            self._teach_item.set_no_show_all(True)
            self._teach_item.set_visible(self._dictionary_enabled)
            menu.append(self._teach_item)
        settings_item = Gtk.MenuItem(label=t("tray.open_settings"))
        settings_item.connect("activate", lambda *_: on_settings())
        menu.append(settings_item)
        self._show_item = None
        if self.on_show_button is not None:
            self._show_item = Gtk.MenuItem(label=t("tray.show_button"))
            self._show_item.connect("activate",
                                    lambda *_: self.on_show_button())
            self._show_item.set_no_show_all(True)
            self._show_item.set_visible(True)
            menu.append(self._show_item)
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

    def popup_at_pointer(self, event):
        """Raise the menu wherever the pointer is.

        Used by the floating button's right-click. popup_at_pointer is
        the only placement that works on Wayland - the older popup()
        wants screen coordinates an application there cannot know.

        "Show the floating button" comes off it: the button is the
        thing that was just clicked.
        """
        if self._show_item is not None:
            self._show_item.set_visible(False)
        self._menu.popup_at_pointer(event)

    def set_dictionary_enabled(self, enabled):
        """Show or hide the teach item, to match the setting.

        Someone who has switched the personal dictionary off entirely
        has no use for an item that only exists to add to it.
        """
        self._dictionary_enabled = bool(enabled)
        if self._teach_item is not None:
            self._teach_item.set_visible(self._dictionary_enabled)

    def _on_left_click(self, _icon):
        if self.on_activate is not None:
            self.on_activate()

    def _on_right_click(self, _icon, button, activate_time):
        if self._show_item is not None:
            self._show_item.set_visible(True)
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
        self._start_indicator()
        self._icon.set_visible(False)
        return False

    def _start_indicator(self):
        """Put the icon up through AppIndicator. True if that worked."""
        # Importing AppIndicator is NOT proof that it works. Only the
        # typelib ships with the bundle; the matching shared library is
        # always the host's, and a GI typelib dlopen()s that library
        # lazily — on the first real call, not at import. So a machine
        # without libayatana-appindicator3 imports cleanly and then throws
        # GError here. Everything through the first call must be guarded,
        # or the tray dies with a traceback and Talk runs invisibly.
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as AppIndicator
            indicator = AppIndicator.Indicator.new(
                "talk", "talk-idle",
                AppIndicator.IndicatorCategory.APPLICATION_STATUS)
            indicator.set_icon_theme_path(ASSET_DIR)
            indicator.set_title("Lightmorphic Talk")
            indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            indicator.set_menu(self._menu)
        except (ImportError, ValueError, GLib.GError, TypeError, AttributeError):
            log.warning(
                "tray unavailable: this desktop does not embed status icons "
                "and libayatana-appindicator3 is missing. The floating button still "
                "works; install libayatana-appindicator3 (and, on GNOME, the "
                "AppIndicator extension) to get the tray icon back.")
            self._indicator = None
            return False

        self._indicator = indicator
        self._set_indicator_state(self._state)
        log.info("tray: AppIndicator icon up (menu only, no left-click)")
        return True

    def _set_indicator_state(self, state):
        icon = _SVG_ICONS.get(state, "talk-idle")
        icon_path = os.path.join(ASSET_DIR, icon + ".svg")
        self._indicator.set_icon_full(
            icon_path, "Lightmorphic Talk — " + t("tray.status." + state))

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
        size = max(16, int(round(self._size * _TRAY_OVERSIZE)))
        self._icon.set_from_pixbuf(
            _draw_frame(size, self._state, self._phase, level,
                        self._progress, fill=_TRAY_FILL, bold_ring=True,
                        badge=self._model_update))
