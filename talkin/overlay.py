"""The on-screen circle indicator.

A small frameless, always-on-top, click-through circle near the bottom
centre of the screen. While listening it shows live waveform lines
moving inside the circle; while transcribing the lines give way to an
arc revolving around the circle. Hidden when idle.
"""

import collections
import math
import threading

import cairo
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

SIZE = 38          # window is square; the circle fills it
BARS = 9           # waveform lines inside the circle
FPS = 30

_BG = (0.07, 0.09, 0.15, 0.92)       # circle fill: Lightmorphic navy
_RING = (1.0, 1.0, 1.0, 0.10)        # faint rim
_WAVE = (0.984, 0.78, 0.067, 1.0)    # Lightmorphic brand yellow #FBC711


class Overlay(Gtk.Window):

    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_default_size(SIZE, SIZE)
        self.set_size_request(SIZE, SIZE)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)

        self._mode = "hidden"          # hidden | listening | thinking
        self._levels = collections.deque([0.0] * BARS, maxlen=BARS)
        self._level_lock = threading.Lock()
        self._phase = 0.0
        self._timer = None

        self.connect("draw", self._draw)
        self.connect("realize", self._click_through)

    # -- public API (call from the GTK main loop) --------------------

    def show_listening(self):
        self._set_mode("listening")

    def show_thinking(self):
        self._set_mode("thinking")

    def hide_overlay(self):
        self._set_mode("hidden")

    def push_level(self, level):
        """Called from the audio thread with the current mic level."""
        with self._level_lock:
            self._levels.append(level)

    # -- internals ---------------------------------------------------

    def _set_mode(self, mode):
        self._mode = mode
        if mode == "hidden":
            if self._timer is not None:
                GLib.source_remove(self._timer)
                self._timer = None
            self.hide()
            return
        if mode == "listening":
            with self._level_lock:
                self._levels.extend([0.0] * BARS)
        self._position()
        self.show_all()
        if self._timer is None:
            self._timer = GLib.timeout_add(1000 // FPS, self._tick)

    def _tick(self):
        self._phase += 0.18
        self.queue_draw()
        return True

    def _position(self):
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geo = monitor.get_geometry()
        x = geo.x + (geo.width - SIZE) // 2
        y = geo.y + geo.height - SIZE - 48
        self.move(x, y)

    def _click_through(self, *_):
        # Empty input region: clicks fall straight through the circle.
        window = self.get_window()
        if window is not None:
            window.input_shape_combine_region(cairo.Region(), 0, 0)

    def _draw(self, _widget, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        cx = cy = SIZE / 2
        radius = SIZE / 2 - 2

        cr.set_source_rgba(*_BG)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.fill()

        cr.set_source_rgba(*_RING)
        cr.set_line_width(1.5)
        cr.arc(cx, cy, radius - 1, 0, 2 * math.pi)
        cr.stroke()

        if self._mode == "listening":
            self._draw_wave(cr, cx, cy, radius)
        elif self._mode == "thinking":
            self._draw_spinner(cr, cx, cy, radius)

    def _draw_wave(self, cr, cx, cy, radius):
        """Vertical lines inside the circle, driven by real mic levels."""
        with self._level_lock:
            levels = list(self._levels)
        inner = radius * 0.62
        step = (inner * 2) / (BARS - 1)
        cr.set_line_width(2.0)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_source_rgba(*_WAVE)
        for i, level in enumerate(levels):
            x = cx - inner + i * step
            # Keep every line inside the circle's chord at this x.
            chord = math.sqrt(max(0.0, radius ** 2 - (x - cx) ** 2))
            wobble = 0.12 + 0.08 * math.sin(self._phase + i * 0.9)
            half = min(chord * 0.72, (wobble + level * 0.85) * inner)
            half = max(half, 1.5)
            cr.move_to(x, cy - half)
            cr.line_to(x, cy + half)
            cr.stroke()

    def _draw_spinner(self, cr, cx, cy, radius):
        """An arc revolving around the inside of the circle."""
        start = self._phase * 1.6
        cr.set_line_width(2.2)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_source_rgba(*_WAVE)
        cr.arc(cx, cy, radius * 0.72, start, start + math.pi * 0.65)
        cr.stroke()
        # A fainter trailing arc for the sense of motion.
        cr.set_source_rgba(_WAVE[0], _WAVE[1], _WAVE[2], 0.25)
        cr.arc(cx, cy, radius * 0.72, start - math.pi * 0.5,
               start - math.pi * 0.08)
        cr.stroke()
