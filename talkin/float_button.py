"""A floating record button you can put anywhere on screen.

The keyboard route is the unreliable one: the compositor sends shortcut
releases both too late and too early, and a mis-timed release either runs
the recording on or cuts it off before a word is spoken. A click has no
such signal to get wrong, which is why this exists.

It draws exactly what the tray icon draws — the same waveform, pulsing
with your voice — so there is one visual language, not two. Click once to
start, click again to stop. A small button on the side opens the
pronunciation popup for teaching it a word.

On Wayland an application cannot place its own window: position is the
compositor's business. So this is draggable — press and move it anywhere
— but it opens wherever the compositor decides, and cannot restore its
own position on a later run.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib

from .i18n import t
from .tray import _draw_frame

log = logging.getLogger("talkin.float")

BUTTON_SIZE = 56       # the round record button
SIDE_SIZE = 26         # the little pronunciation button beside it
_FPS_MS = 100

# Right at the bottom edge of the usable area. The work area already
# excludes panels and docks, so this is a hair of breathing room rather
# than a margin.
_BOTTOM_GAP = 6

_ANIMATED = {"listening", "thinking", "loading", "downloading"}


class FloatButton(Gtk.Window):
    """A small always-on-top record button."""

    def __init__(self, on_toggle, on_correction, dictionary_enabled=True):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.on_toggle = on_toggle
        self.on_correction = on_correction
        self._state = "loading"
        self._phase = 0.0
        self._level = 0.0
        self._progress = 0.0
        self._timer = None

        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_app_paintable(True)
        # Never take keyboard focus. Text is typed into whatever window
        # the compositor considers focused at that moment, and an app
        # cannot aim it anywhere else — so if clicking this button stole
        # focus, every dictation would land in this button instead of the
        # document being written in.
        self.set_accept_focus(False)
        self.set_focus_on_map(False)

        # Transparent background where the circle does not cover, so it
        # reads as a floating control rather than a grey square.
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.add(row)

        self.canvas = Gtk.DrawingArea()
        self.canvas.set_size_request(BUTTON_SIZE, BUTTON_SIZE)
        # No tooltip here on purpose. The button sits wherever it was
        # dragged, directly under the pointer that just clicked it, so a
        # tooltip pops up over the work every time — and it only repeats
        # what the colour and waveform already say. The tray icon keeps
        # the wording for anyone who wants it spelled out.
        self.canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                               | Gdk.EventMask.BUTTON_RELEASE_MASK)
        self.canvas.connect("draw", self._on_draw)
        self.canvas.connect("button-press-event", self._on_press)
        self.canvas.connect("button-release-event", self._on_release)
        row.pack_start(self.canvas, False, False, 0)

        # Drawn, not a themed label: the window is transparent, so a
        # label takes the desktop theme's text colour and disappears
        # against a dark background. Brand navy disc, brand yellow
        # letters, same as the record button beside it.
        teach = Gtk.EventBox()
        teach.set_visible_window(False)
        teach.set_tooltip_text(t("float.teach"))
        teach_canvas = Gtk.DrawingArea()
        teach_canvas.set_size_request(SIDE_SIZE, SIDE_SIZE)
        teach_canvas.connect("draw", self._draw_teach)
        teach.add(teach_canvas)
        teach.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        teach.connect("button-press-event",
                      lambda *_a: (self.on_correction(), True)[1])
        row.pack_start(teach, False, False, 0)
        row.set_valign(Gtk.Align.CENTER)
        self.teach = teach

        # Press-and-move drags the window; a press that does not move is
        # treated as a click. The compositor owns the drag on Wayland.
        self._press_x = self._press_y = 0
        self._dragged = False

        # GNOME on Wayland treats "keep above" as a hint and mostly
        # ignores it, so a full-screen-ish window such as a browser ends
        # up covering this. Re-asserting it periodically is the only
        # lever an ordinary app has; it costs nothing and helps on the
        # compositors that do listen. It cannot steal focus, because the
        # window never accepts any.
        GLib.timeout_add_seconds(3, self._stay_on_top)

        self.connect("delete-event", lambda *_a: self.hide() or True)
        self.show_all()
        self.set_teach_visible(dictionary_enabled)
        self.present()
        self.place_default()
        log.info("floating button shown=%s size=%sx%s",
                 self.get_visible(), *self.get_size())
        self._sync_timer()

    def set_teach_visible(self, enabled):
        """Show or hide the little "teach a word" button.

        Someone who has switched the personal dictionary off entirely
        has no use for a button that only exists to add to it - leaving
        it there would be a control that does something they just said
        they do not want.
        """
        if enabled:
            self.teach.show()
            self.set_size_request(BUTTON_SIZE + SIDE_SIZE + 12, BUTTON_SIZE)
        else:
            self.teach.hide()
            self.set_size_request(BUTTON_SIZE, BUTTON_SIZE)

    def place_default(self):
        """Put the button near the bottom middle of the screen.

        The middle is the worst place to open: it lands over whatever is
        being read or written, and against a busy wallpaper it is hard to
        pick out at all. Bottom centre is clear of the work, clear of the
        panel, and always in the same place.

        Whether this is obeyed depends on the display system. On X11 and
        XWayland it is exact; on native Wayland an application is not
        permitted to place its own window and the call does nothing, so
        the button opens wherever the compositor decides.
        """
        try:
            display = self.get_display()
            monitor = (display.get_primary_monitor()
                       or display.get_monitor_at_window(self.get_window())
                       or display.get_monitor(0))
            area = monitor.get_workarea()
            width, height = self.get_size()
            if width < 2 or height < 2:      # not yet realised
                width = BUTTON_SIZE + SIDE_SIZE + 12
                height = BUTTON_SIZE
            x = area.x + (area.width - width) // 2
            y = area.y + area.height - height - _BOTTOM_GAP
            self.move(x, y)
            log.info("floating button placed at %s,%s", x, y)
            # And again once the window manager has finished mapping it.
            # A move issued in the same breath as the map is routinely
            # overridden by the placement the window manager had already
            # decided on, which is what put it back in the middle every
            # time it was re-shown.
            GLib.timeout_add(180, lambda: (self.move(x, y), False)[1])
        except Exception:
            log.debug("could not place the floating button", exc_info=True)

    # -- interaction -----------------------------------------------------

    def _on_press(self, _widget, event):
        self._press_x, self._press_y = event.x_root, event.y_root
        self._dragged = False
        if event.button == 1:
            # Start a compositor-driven move; if the pointer never moves
            # this does nothing and the release below counts as a click.
            self.begin_move_drag(event.button, int(event.x_root),
                                 int(event.y_root), event.time)
        return True

    def _on_release(self, _widget, event):
        moved = (abs(event.x_root - self._press_x) > 4
                 or abs(event.y_root - self._press_y) > 4)
        if not moved and event.button == 1:
            self.on_toggle()
        return True

    # -- state -----------------------------------------------------------

    def set_state(self, state):
        self._state = state
        self._sync_timer()
        self.canvas.queue_draw()

    def set_level(self, level):
        self._level = level

    def set_progress(self, fraction):
        self._progress = fraction

    def _sync_timer(self):
        want = self._state in _ANIMATED
        if want and self._timer is None:
            self._timer = GLib.timeout_add(_FPS_MS, self._tick)
        elif not want and self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None

    def _stay_on_top(self):
        if not self.get_visible():
            return True
        try:
            self.set_keep_above(True)
            window = self.get_window()
            if window is not None:
                window.raise_()
        except Exception:
            pass
        return True

    def _tick(self):
        self._phase += 0.35
        self.canvas.queue_draw()
        return True

    def _draw_teach(self, _widget, cr):
        import math
        s = SIDE_SIZE / 26.0
        cr.set_source_rgb(0x11 / 255, 0x18 / 255, 0x27 / 255)
        cr.arc(13 * s, 13 * s, 12 * s, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgb(0xFB / 255, 0xC7 / 255, 0x11 / 255)
        cr.select_font_face("Manrope")
        cr.set_font_size(12 * s)
        label = "Aa"
        ext = cr.text_extents(label)
        cr.move_to(13 * s - ext.width / 2 - ext.x_bearing,
                   13 * s - ext.height / 2 - ext.y_bearing)
        cr.show_text(label)
        return False

    def _on_draw(self, _widget, cr):
        pixbuf = _draw_frame(BUTTON_SIZE, self._state, self._phase,
                             self._level, self._progress)
        Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
        cr.paint()
        return False

    def stop(self):
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None
        self.destroy()
