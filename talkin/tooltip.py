"""A custom speech-bubble tooltip: a dark rounded box with a small
triangular pointer aimed at whatever widget it's attached to.

GTK's native tooltip is a plain undecorated rectangle with no arrow and
no awareness of screen edges - it just clips or overlaps when there's no
room. This one measures the available space on show and flips the arrow
between the top edge (bubble below the widget) and the bottom edge
(bubble above it) so the whole thing always stays on-screen, the same
way a well-behaved tooltip on any other platform does.

Usage: call attach(widget, text) once per widget; call it again with new
text any time the tooltip's content changes (e.g. an "armed" destructive
button, or the update dot's state) - if that widget's bubble happens to
be showing right now, it's redrawn immediately with the new text.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import math

import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo

# Light bubble, dark text - the inverse of Talkin's own dark chrome, so
# the tooltip actually stands out against it instead of blending in.
# Text is the brand navy (#111827), not an arbitrary dark grey.
_BG = (0.98, 0.98, 0.98, 1.0)
_FG = (0x11 / 255, 0x18 / 255, 0x27 / 255, 1.0)
# 25% smaller across the board (radius/arrow/padding/font all scaled
# down together, not just the text) so the bubble shrinks as a whole
# rather than getting text that's cramped against unchanged padding.
_RADIUS = 6
_ARROW_W = 10.5
_ARROW_H = 6
_PAD_X = 9
_PAD_Y = 5.25
_FONT_SIZE_PT = 7.5
_GAP = 6                 # gap between the anchor widget and the bubble tip
_SCREEN_MARGIN = 8        # keep this far from the monitor's own edge
_HOVER_DELAY_MS = 400

_hover_timeout = None
_bubble = None


def _place(ax, ay, aw, ah, bubble_w, bubble_h,
           work_x, work_y, work_w, work_h):
    """Pure placement math, kept separate from any real GTK/GDK window
    so it can be tested directly against synthetic coordinates rather
    than fighting a real window manager's own edge-clamping behaviour.

    ax/ay/aw/ah: the anchor widget's on-screen rect.
    bubble_w/bubble_h: the bubble's own body size (excluding the arrow).
    work_*: the monitor's usable work area.
    Returns (arrow_up, win_x, win_y, arrow_x).
    """
    total_h = bubble_h + _ARROW_H
    anchor_cx = ax + aw // 2

    # Default above the anchor, arrow pointing down at it - only drop
    # to below (arrow pointing up) when there isn't room above, e.g.
    # the anchor sits right at the top of the screen.
    below_y = ay + ah + _GAP
    above_y = ay - _GAP - total_h
    fits_above = above_y >= work_y

    if fits_above:
        arrow_up = False
        win_y = above_y
    else:
        arrow_up = True
        win_y = below_y
        # Neither direction has room (a tiny screen/monitor) - clamp
        # inside the work area rather than run off it.
        if win_y + total_h > work_y + work_h:
            win_y = work_y + work_h - total_h

    win_x = anchor_cx - bubble_w // 2
    win_x = max(work_x + _SCREEN_MARGIN,
                min(win_x, work_x + work_w - _SCREEN_MARGIN - bubble_w))

    # The arrow stays aimed at the anchor's centre even when the bubble
    # itself has been shifted sideways to stay on-screen - clamped so
    # its tip never slides past the rounded corners.
    arrow_x = anchor_cx - win_x
    arrow_x = max(_RADIUS + _ARROW_W / 2,
                  min(arrow_x, bubble_w - _RADIUS - _ARROW_W / 2))

    return arrow_up, win_x, win_y, arrow_x


class _Bubble:
    def __init__(self):
        self.win = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.win.set_type_hint(Gdk.WindowTypeHint.TOOLTIP)
        self.win.set_decorated(False)
        self.win.set_resizable(False)
        self.win.set_skip_taskbar_hint(True)
        self.win.set_skip_pager_hint(True)
        self.win.set_app_paintable(True)
        screen = Gdk.Screen.get_default()
        visual = screen.get_rgba_visual() if screen else None
        if visual is not None:
            self.win.set_visual(visual)
        self.win.connect("draw", self._on_draw)
        self.current_anchor = None
        self._layout = None
        self._arrow_up = True
        self._arrow_x = _ARROW_W
        self._bubble_w = 0
        self._bubble_h = 0

    def show_for(self, anchor, text):
        if anchor.get_window() is None or not text:
            self.hide()
            return
        alloc = anchor.get_allocation()
        # translate_coordinates + the toplevel's own screen origin,
        # rather than the widget's own GdkWindow origin - several
        # widgets used here (buttons inside a GtkEventBox, the update
        # dot's DrawingArea) share a GdkWindow with siblings, so their
        # own window origin alone isn't their actual on-screen position.
        ax, ay = anchor.translate_coordinates(anchor.get_toplevel(), 0, 0)
        toplevel_window = anchor.get_toplevel().get_window()
        _ok, twx, twy = toplevel_window.get_origin()
        ax += twx
        ay += twy
        aw, ah = alloc.width, alloc.height

        display = anchor.get_display()
        monitor = display.get_monitor_at_point(ax + aw // 2, ay + ah // 2)
        work = monitor.get_workarea()

        layout = self.win.create_pango_layout(text)
        font = Pango.FontDescription()
        font.set_family("Manrope")
        font.set_weight(Pango.Weight.BOLD)
        # Fractional point size needs set_size() in Pango units - a
        # "Manrope 7.5" string wouldn't parse the fraction.
        font.set_size(int(_FONT_SIZE_PT * Pango.SCALE))
        layout.set_font_description(font)
        text_w, text_h = layout.get_pixel_size()
        self._layout = layout

        bubble_w = text_w + int(_PAD_X * 2)
        bubble_h = text_h + int(_PAD_Y * 2)

        arrow_up, win_x, win_y, arrow_x = _place(
            ax, ay, aw, ah, bubble_w, bubble_h,
            work.x, work.y, work.width, work.height)

        self._arrow_up = arrow_up
        self._arrow_x = arrow_x
        self._bubble_w = bubble_w
        self._bubble_h = bubble_h
        self.current_anchor = anchor

        # set_size_request, not resize() - this window has no child
        # widget of its own (everything is drawn manually via "draw"),
        # so there's nothing to give it a natural size on first show.
        # resize() is only a hint on top of that natural size, so on an
        # as-yet-unrealized window it was silently ignored, leaving the
        # popup at GTK's ~200x200 fallback regardless of the real text.
        self.win.set_size_request(bubble_w, bubble_h + _ARROW_H)
        self.win.move(int(win_x), int(win_y))
        self.win.show_all()
        self.win.queue_draw()

    def hide(self):
        self.current_anchor = None
        self.win.hide()

    def _on_draw(self, _widget, cr):
        cr.save()
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.restore()

        w, h = self._bubble_w, self._bubble_h
        r = _RADIUS
        body_y0 = _ARROW_H if self._arrow_up else 0
        body_y1 = body_y0 + h
        ax = self._arrow_x

        cr.new_path()
        cr.arc(r, body_y0 + r, r, math.pi, 1.5 * math.pi)
        cr.arc(w - r, body_y0 + r, r, 1.5 * math.pi, 2 * math.pi)
        cr.arc(w - r, body_y1 - r, r, 0, 0.5 * math.pi)
        cr.arc(r, body_y1 - r, r, 0.5 * math.pi, math.pi)
        cr.close_path()

        if self._arrow_up:
            cr.move_to(ax - _ARROW_W / 2, body_y0)
            cr.line_to(ax, 0)
            cr.line_to(ax + _ARROW_W / 2, body_y0)
        else:
            cr.move_to(ax - _ARROW_W / 2, body_y1)
            cr.line_to(ax, h + _ARROW_H)
            cr.line_to(ax + _ARROW_W / 2, body_y1)
        cr.close_path()

        cr.set_source_rgba(*_BG)
        cr.fill()

        cr.set_source_rgba(*_FG)
        cr.move_to(_PAD_X, body_y0 + _PAD_Y)
        PangoCairo.show_layout(cr, self._layout)
        return False


def _get_bubble():
    global _bubble
    if _bubble is None:
        _bubble = _Bubble()
    return _bubble


def _cancel_pending():
    global _hover_timeout
    if _hover_timeout is not None:
        GLib.source_remove(_hover_timeout)
        _hover_timeout = None


def _show_now(widget):
    global _hover_timeout
    _hover_timeout = None
    text = getattr(widget, "_bubble_text", None)
    if text:
        _get_bubble().show_for(widget, text)
    return False


def _on_enter(widget, _event):
    _cancel_pending()
    global _hover_timeout
    if getattr(widget, "_bubble_text", None):
        _hover_timeout = GLib.timeout_add(_HOVER_DELAY_MS, _show_now, widget)
    return False


def _on_leave(_widget, _event):
    _cancel_pending()
    _get_bubble().hide()
    return False


def _on_press(_widget, _event):
    _cancel_pending()
    _get_bubble().hide()
    return False


def attach(widget, text):
    """Attach this bubble tooltip to `widget`, replacing its native one.

    Safe to call repeatedly on the same widget to update the text (the
    wiring only happens once) - if that widget's bubble is showing right
    now, it's redrawn immediately with the new text.
    """
    widget.set_has_tooltip(False)
    widget._bubble_text = text
    if not getattr(widget, "_bubble_wired", False):
        widget._bubble_wired = True
        widget.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK)
        widget.connect("enter-notify-event", _on_enter)
        widget.connect("leave-notify-event", _on_leave)
        widget.connect("button-press-event", _on_press)
    bubble = _get_bubble()
    if bubble.current_anchor is widget:
        bubble.show_for(widget, text)


def get_text(widget):
    return getattr(widget, "_bubble_text", None)
