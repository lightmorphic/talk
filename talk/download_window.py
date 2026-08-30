"""The first-run model download, said once and quietly.

Talk needs a ~600 MB speech model before it can do anything, fetched
once on first run. Doing that behind a spinning tray icon is
indefensible — a stalled transfer looks exactly like a broken app — but
the answer is not a large window either. A small notice says what is
happening; the tray icon carries the progress from then on, as a ring
that fills rather than a spinner that only says "busy".

Closing the notice does not cancel anything. The download continues and
the ring keeps filling, which is the point of putting progress there.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import os
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib

from .config import MODEL_DIR
from .inject_portal import RESTORE_TOKEN_KEY
from .i18n import t

log = logging.getLogger("talk.download")

# The model is a little over 600 MB. Only used to draw the bar and ring;
# the MB counter always shows real bytes, so an imprecise total can make
# the bar slightly wrong but never the number.
EXPECTED_BYTES = 620 * 1024 * 1024

_POLL_MS = 700

# No bytes for this long means the transfer has stalled. The host
# throttles hard after repeated pulls and a stall can last minutes, so
# say so rather than letting it look frozen.
STALL_AFTER_S = 20


def cache_bytes():
    """Bytes downloaded so far, counted straight off the disk."""
    total = 0
    for root, _dirs, files in os.walk(MODEL_DIR):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total

# House style, same tokens as the Settings window: solid panels, one
# hairline border, one soft shadow, generous air. No gradients, no
# translucency — depth comes from the border and the shadow.
_CSS = b"""
window.talk-firstrun {
  background-color: #09090b;
  color: #fafafa;
  font-family: "Manrope", sans-serif;
}
.talk-firstrun label { color: #fafafa; }
.talk-firstrun .title {
  font-size: 1.375rem; font-weight: 700; letter-spacing: -0.02em;
}
.talk-firstrun .lede { color: #a1a1aa; font-size: 0.9375rem; }
.talk-firstrun .card {
  background-color: #1b1d29;
  border: 1px solid alpha(#ffffff, 0.09);
  border-radius: 1.375rem;
  padding: 16px;
}
.talk-firstrun .card-title { font-weight: 600; font-size: 1rem; }
.talk-firstrun .card-body { color: #a1a1aa; font-size: 0.9375rem; }
.talk-firstrun .step {
  color: #645007; background-color: #fbc711;
  border-radius: 999px; font-weight: 700; font-size: 0.8125rem;
  padding: 1px 9px;
}
.talk-firstrun .warning-card {
  background-color: #350f0c;
  border: 1px solid #f34236;
  border-radius: 1.375rem;
  padding: 14px;
}
.talk-firstrun .warning-card label { color: #f34236; font-weight: 600; }
.talk-firstrun .status { color: #a1a1aa; font-size: 0.875rem; }
.talk-firstrun button.choice {
  background-color: #1b1d29;
  background-image: none;
  border: 1px solid alpha(#ffffff, 0.12);
  border-radius: 0.875rem;
  color: #fafafa;
  padding: 5px 12px;
  min-width: 200px;
}
.talk-firstrun button.choice:hover { background-color: #272a3a; }
list.choice-list { background-color: #1b1d29; }
list.choice-list row { border-radius: 0.625rem; }
list.choice-list row:selected {
  background-color: #fbc711; color: #111827; text-shadow: none;
}
list.choice-list row:selected label { color: #111827; text-shadow: none; }
"""


def _apply_style(widget):
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


# -- small drawn glyphs ------------------------------------------------
# Drawn rather than written, because a picture of the switch and the
# button says in a glance what a paragraph says slowly. Cairo keeps them
# crisp at any scale and adds no image files to the bundle.

_NAVY = (0x11 / 255, 0x18 / 255, 0x27 / 255)
_YELLOW = (0xFB / 255, 0xC7 / 255, 0x11 / 255)


class Glyph(Gtk.DrawingArea):
    """A tiny diagram: 'toggle', 'click' or 'teach'."""

    def __init__(self, kind, size=34):
        super().__init__()
        self.kind = kind
        self.size = size
        self.set_size_request(size, size)
        self.connect("draw", self._draw)

    def _draw(self, _w, cr):
        s = self.size / 34.0
        if self.kind == "toggle":
            # a switch, turned on
            cr.set_source_rgb(*_YELLOW)
            cr.new_sub_path()
            cr.arc(11 * s, 17 * s, 7 * s, 0, 2 * 3.14159)
            cr.arc(23 * s, 17 * s, 7 * s, 0, 2 * 3.14159)
            cr.rectangle(11 * s, 10 * s, 12 * s, 14 * s)
            cr.fill()
            cr.set_source_rgb(1, 1, 1)
            cr.arc(23 * s, 17 * s, 5 * s, 0, 2 * 3.14159)
            cr.fill()
        elif self.kind == "click":
            # the record button with a pointer on it
            cr.set_source_rgb(*_NAVY)
            cr.arc(15 * s, 15 * s, 11 * s, 0, 2 * 3.14159)
            cr.fill()
            cr.set_source_rgb(*_YELLOW)
            cr.set_line_width(1.8 * s)
            for i, x in enumerate((10, 13, 16, 19)):
                half = (3, 6, 4.5, 2.5)[i] * s
                cr.move_to(x * s, 15 * s - half)
                cr.line_to(x * s, 15 * s + half)
                cr.stroke()
            cr.set_source_rgb(1, 1, 1)
            cr.move_to(20 * s, 20 * s)
            cr.line_to(20 * s, 32 * s)
            cr.line_to(24 * s, 28 * s)
            cr.line_to(30 * s, 30 * s)
            cr.line_to(24 * s, 24 * s)
            cr.close_path()
            cr.fill_preserve()
            cr.set_source_rgb(*_NAVY)
            cr.set_line_width(1.2 * s)
            cr.stroke()
        else:  # teach
            # The same record button, with "R" beside it for the right
            # mouse button — the gesture that opens the teach-a-word
            # popup, now that it is not a second button of its own.
            import math
            cr.set_source_rgb(*_NAVY)
            cr.arc(13 * s, 17 * s, 11 * s, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgb(*_YELLOW)
            cr.set_line_width(1.6 * s)
            for i, x in enumerate((9, 12, 15, 18)):
                half = (2.5, 5, 3.5, 2)[i] * s
                cr.move_to(x * s, 17 * s - half)
                cr.line_to(x * s, 17 * s + half)
                cr.stroke()
            cr.arc(28 * s, 25 * s, 6 * s, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgb(*_NAVY)
            cr.select_font_face("Manrope")
            cr.set_font_size(9 * s)
            label = "R"
            ext = cr.text_extents(label)
            cr.move_to(28 * s - ext.width / 2 - ext.x_bearing,
                       25 * s - ext.height / 2 - ext.y_bearing)
            cr.show_text(label)
        return False


def _card(kind, step, title, body):
    """One instruction: a numbered step, a diagram, a line of plain words."""
    card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
    card.get_style_context().add_class("card")

    card.pack_start(Glyph(kind), False, False, 0)

    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    number = Gtk.Label(label=str(step))
    number.get_style_context().add_class("step")
    number.set_valign(Gtk.Align.CENTER)
    head.pack_start(number, False, False, 0)
    heading = Gtk.Label(label=title, xalign=0)
    heading.get_style_context().add_class("card-title")
    head.pack_start(heading, False, False, 0)
    text.pack_start(head, False, False, 0)

    line = Gtk.Label(label=body, xalign=0, wrap=True)
    line.set_max_width_chars(40)
    line.get_style_context().add_class("card-body")
    text.pack_start(line, False, False, 0)

    card.pack_start(text, True, True, 0)
    return card


# -- the filling circle ------------------------------------------------

class WaterCircle(Gtk.DrawingArea):
    """A circle that fills with moving liquid as the download arrives.

    A progress bar states a number; this shows a quantity. The surface
    keeps moving even while the transfer is stalled, which is the honest
    signal — something is still waiting, as opposed to a frozen bar that
    looks like a hung app.
    """

    SIZE = 132

    def __init__(self):
        super().__init__()
        self.fraction = 0.0
        self.phase = 0.0
        self.set_size_request(self.SIZE, self.SIZE)
        self.connect("draw", self._draw)
        GLib.timeout_add(60, self._tick)

    def _tick(self):
        self.phase += 0.09
        self.queue_draw()
        return True

    def _draw(self, _w, cr):
        import math
        size = self.SIZE
        cx = cy = size / 2.0
        r = size / 2.0 - 6

        dark = _is_dark()
        # Brand navy well in both themes: the water reads as brand yellow
        # against it, and the shape stays identity rather than surface.
        cr.set_source_rgb(*_NAVY)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.fill()

        cr.save()
        cr.arc(cx, cy, r - 3, 0, 2 * math.pi)
        cr.clip()

        level = cy + r - (2 * r * max(0.0, min(1.0, self.fraction)))
        amp = 4.0 if self.fraction < 1.0 else 1.5

        for band, (alpha, speed, shift) in enumerate(
                ((0.35, 1.0, 0.0), (1.0, 1.4, 1.1))):
            cr.set_source_rgba(*_YELLOW, alpha)
            cr.move_to(cx - r, size)
            x = cx - r
            while x <= cx + r:
                y = level + amp * math.sin(
                    (x / 26.0) + self.phase * speed + shift)
                cr.line_to(x, y)
                x += 3
            cr.line_to(cx + r, size)
            cr.close_path()
            cr.fill()
        cr.restore()

        # A hairline rim, the same one-pixel border panels get.
        cr.set_source_rgba(1, 1, 1, 0.16 if dark else 0.10)
        cr.set_line_width(1.5)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.stroke()

        percent = int(max(0.0, min(1.0, self.fraction)) * 100)
        label = "{}%".format(percent)
        cr.select_font_face("Manrope")
        cr.set_font_size(size * 0.21)
        extents = cr.text_extents(label)
        # White reads on navy above the waterline and on yellow below it;
        # a mid-grey would fail against one or the other.
        cr.set_source_rgb(1, 1, 1)
        cr.move_to(cx - extents.width / 2 - extents.x_bearing,
                   cy - extents.height / 2 - extents.y_bearing)
        cr.show_text(label)
        return False


def _is_dark():
    settings = Gtk.Settings.get_default()
    if settings is None:
        return True
    try:
        return bool(settings.get_property("gtk-application-prefer-dark-theme"))
    except Exception:
        return True


class DownloadWindow(Gtk.Window):
    """A small, quiet notice that a one-time download is running."""

    def __init__(self, config, on_progress=None, on_dismissed=None,
                 on_quit=None, is_ready=None, on_language_changed=None):
        super().__init__(title=t("download.title"))
        self.config = config
        self.on_progress = on_progress
        self.on_dismissed = on_dismissed
        self.on_quit = on_quit
        self.is_ready = is_ready
        self.on_language_changed = on_language_changed
        self._armed = None      # timer while "click again to quit" stands
        self._failed = False
        self.on_retry = None
        self._complete = False
        self._last_bytes = 0
        self._last_change = time.monotonic()
        self._timer = None

        self.set_default_size(340, -1)
        self.set_resizable(False)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_keep_above(True)

        _apply_style(self)
        self.get_style_context().add_class("talk-firstrun")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        self.add(box)

        # The circle carries the status, so it leads and everything else
        # explains. Centred, with air around it rather than a heading
        # stacked on a bar stacked on a line.
        self.circle = WaterCircle()
        circle_holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        circle_holder.set_halign(Gtk.Align.CENTER)
        circle_holder.pack_start(self.circle, False, False, 0)
        box.pack_start(circle_holder, False, False, 4)

        # The language chooser belongs here, not only in Settings. This
        # window is the first thing anyone sees, it is the one that must
        # be understood — it carries the permission step — and Settings
        # is not reachable until it is done with.
        box.pack_start(self._language_row(), False, False, 0)

        title = Gtk.Label(label=t("download.heading"), xalign=0.5)
        title.get_style_context().add_class("title")
        box.pack_start(title, False, False, 0)

        self.status = Gtk.Label(xalign=0.5, wrap=True)
        self.status.set_max_width_chars(44)
        self.status.get_style_context().add_class("status")
        box.pack_start(self.status, False, False, 0)

        self.warning_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.warning_card.get_style_context().add_class("warning-card")
        self.warning = Gtk.Label(xalign=0, wrap=True)
        self.warning.set_max_width_chars(44)
        self.warning_card.pack_start(self.warning, True, True, 0)
        self.warning_card.set_no_show_all(True)
        box.pack_start(self.warning_card, False, False, 0)

        # The permission step is a Wayland thing: the compositor has to
        # be asked before anything may type. On X11 there is nothing to
        # grant, so showing the step — let alone refusing to close until
        # it is done — would be asking for a switch that does not exist.
        step = 1
        if self.needs_permission:
            box.pack_start(_card("toggle", step,
                                 t("firstrun.permission_title"),
                                 t("firstrun.permission")), False, False, 0)
            step += 1
        box.pack_start(_card("click", step, t("firstrun.click_title"),
                             t("firstrun.click")), False, False, 0)
        box.pack_start(_card("teach", step + 1, t("firstrun.teach_title"),
                             t("firstrun.teach")), False, False, 0)

        self.retry_button = Gtk.Button(label=t("download.retry"))
        self.retry_button.get_style_context().add_class("suggested-action")
        self.retry_button.set_halign(Gtk.Align.CENTER)
        self.retry_button.set_no_show_all(True)
        self.retry_button.connect("clicked", self._on_retry)
        box.pack_start(self.retry_button, False, False, 0)

        # Every route out goes through _dismiss: the window button, Esc,
        # and the compositor's own close. Nothing should be able to make
        # this window vanish while Talk still cannot type.
        self.connect("delete-event", self._dismiss)
        self.connect("key-press-event", self._on_key)

        self._update()
        self._timer = GLib.timeout_add(_POLL_MS, self._update)
        self.show_all()

    def _language_row(self):
        """The language chooser, centred above everything else."""
        from . import chooser, i18n
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.set_halign(Gtk.Align.CENTER)
        button = chooser.choice_button(
            i18n.available_languages(), self.config.get("language"),
            lambda code: self._on_language(None, code),
            searchable=True, filter_placeholder=i18n.t("settings.filter"))
        row.pack_start(button, False, False, 0)
        return row

    def _on_language(self, _button, code):
        """Switch language and redraw this window in it."""
        from . import i18n
        if code == self.config.get("language"):
            return
        self.config.update({"language": code})
        i18n.set_language(code)
        if self.on_language_changed is not None:
            self.on_language_changed()

    @property
    def needs_permission(self):
        """Whether this desktop has a permission step at all."""
        from . import session
        return session.is_wayland()

    def permission_granted(self):
        """True only once Talk can actually type.

        Asks the injector whether its session is live, rather than
        whether a permission token was stored. A stored token means the
        desktop agreed at some point in the past; it does not mean the
        session started this time, and the window was closing on the
        strength of it while the app still could not type a word.
        """
        if not self.needs_permission:
            return True
        if self.is_ready is None:
            return bool(self.config.get(RESTORE_TOKEN_KEY))
        return bool(self.is_ready())

    def snapshot(self):
        """What a rebuilt window needs in order to carry on unchanged."""
        return {"complete": self._complete, "failed": self._failed,
                "fraction": self.circle.fraction, "retry": self.on_retry}

    def restore(self, state):
        self._complete = state["complete"]
        self.circle.fraction = state["fraction"]
        if state["failed"]:
            self.fail(t("download.failed"), state["retry"])

    def _dismiss(self, *_args):
        # Closing before granting permission leaves an app that hears
        # perfectly and can never type, with nothing on screen to explain
        # why. Say so in place rather than stacking another dialog on
        # top — and never trap anyone: a second click quits Talk
        # outright, which is the honest way out for someone who has
        # decided against it.
        if self._failed:
            # Nothing to insist on while the download is broken: holding
            # the window open would trap someone with no way forward.
            self.hide()
            if self.on_dismissed is not None:
                self.on_dismissed()
            return True
        if not self.permission_granted():
            if self._armed is not None:
                GLib.source_remove(self._armed)
                self._armed = None
                if self.on_quit is not None:
                    self.on_quit()
                return True
            self.warning.set_text(t("firstrun.must_allow"))
            self.warning_card.show()
            self.warning.show()
            self._armed = GLib.timeout_add_seconds(8, self._disarm)
            return True
        self.hide()
        if self.on_dismissed is not None:
            self.on_dismissed()
        return True   # keep the window alive so progress keeps updating

    def _on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self._dismiss()
            return True
        return False

    def _disarm(self):
        self._armed = None
        self.warning_card.hide()
        return False

    def _update(self):
        if self._complete:
            self.circle.fraction = 1.0
            self.status.set_text(t("firstrun.waiting_permission"))
            if self.permission_granted():
                if self._timer is not None:
                    GLib.source_remove(self._timer)
                    self._timer = None
                self.destroy()
                return False
            return True
        done = cache_bytes()
        now = time.monotonic()
        if done != self._last_bytes:
            self._last_bytes = done
            self._last_change = now

        fraction = min(1.0, done / float(EXPECTED_BYTES))
        self.circle.fraction = fraction
        if self.on_progress is not None:
            self.on_progress(fraction)

        megabytes = int(done / (1024 * 1024))
        expected = int(EXPECTED_BYTES / (1024 * 1024))
        stalled_for = now - self._last_change
        if stalled_for > STALL_AFTER_S:
            self.status.set_text(t("download.stalled").format(
                done=megabytes, total=expected, seconds=int(stalled_for)))
        else:
            self.status.set_text(t("download.progress").format(
                done=megabytes, total=expected, percent=int(fraction * 100)))
        return True

    def fail(self, message, on_retry=None):
        """The download gave up. Say so, and offer to try again.

        This window used to be closed on failure by the same call that
        closes it on success, so a download that had died looked exactly
        like one that had finished — the window vanished, the icon showed
        a pause mark, and nothing said what had happened or what to do.
        """
        self._failed = True
        self.on_retry = on_retry
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None
        self.status.set_text(message)
        self.warning.set_text(t("download.failed_help"))
        self.warning_card.show()
        self.warning.show()
        self.retry_button.show()
        self.present()

    def _on_retry(self, *_args):
        self._failed = False
        self.retry_button.hide()
        self.warning_card.hide()
        self.status.set_text(t("download.retrying"))
        if self._timer is None:
            self._timer = GLib.timeout_add_seconds(1, self._update)
        if self.on_retry is not None:
            self.on_retry()

    def finish(self):
        """The model is ready.

        Closes only if permission has been given. Otherwise the window
        stays at 100% with the permission step still on it: vanishing at
        that moment is precisely when the one remaining instruction gets
        lost, and the app then does nothing with no explanation.
        """
        self._complete = True
        if self.on_progress is not None:
            self.on_progress(1.0)
        self.circle.fraction = 1.0
        if self.permission_granted():
            if self._timer is not None:
                GLib.source_remove(self._timer)
                self._timer = None
            self.destroy()
            return False
        self.status.set_text(t("firstrun.waiting_permission"))
        self.present()
        return False
