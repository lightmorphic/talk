"""The teach-a-word popup.

Triggered by the correction hotkey: reads whatever text is highlighted
anywhere on screen (X11 primary selection), asks how it should have
been spelt, and saves the pair to the personal dictionary.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from . import injector
from .i18n import t


def open_correction(dictionary, notify):
    heard = (injector.read_primary_selection() or "").strip()
    if not heard or len(heard) > 80:
        notify(t("correction.no_selection"))
        return

    dialog = Gtk.Dialog(title=t("correction.title"))
    dialog.set_keep_above(True)
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.set_default_size(360, -1)
    dialog.add_button(t("correction.cancel"), Gtk.ResponseType.CANCEL)
    dialog.add_button(t("correction.save"), Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)

    box = dialog.get_content_area()
    box.set_spacing(8)
    box.set_margin_top(14)
    box.set_margin_bottom(14)
    box.set_margin_start(16)
    box.set_margin_end(16)

    heard_label = Gtk.Label()
    heard_label.set_markup("{}:  <b>{}</b>".format(
        GLib.markup_escape_text(t("correction.heard")),
        GLib.markup_escape_text(heard)))
    heard_label.set_xalign(0)
    box.pack_start(heard_label, False, False, 0)

    ask = Gtk.Label(label=t("correction.should_be"))
    ask.set_xalign(0)
    box.pack_start(ask, False, False, 0)

    entry = Gtk.Entry()
    entry.set_text(heard)
    entry.set_activates_default(True)
    box.pack_start(entry, False, False, 0)

    dialog.show_all()
    entry.grab_focus()
    entry.select_region(0, -1)

    def on_response(dlg, response):
        if response == Gtk.ResponseType.OK:
            say = entry.get_text().strip()
            if say and say != heard:
                dictionary.add(heard, say)
                notify(t("correction.saved"))
        dlg.destroy()

    dialog.connect("response", on_response)
