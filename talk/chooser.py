"""A dropdown that behaves the way people expect one to.

GTK's own combo box opens on button-press and closes again on the
matching release, so a plain click flashes the list open and shut unless
you hold the mouse down and drag onto the item you want. That is how it
has always worked — it dates from menus that behaved that way — but
nothing else on a modern desktop does, and it reads as a list refusing
to stay open.

This is a button with a popover list instead: click to open, click to
choose. With `searchable`, a filter box sits at the top, which is what
makes a list of twenty-five languages usable.

Nothing here asks the icon theme for anything. Inside the AppImage a
missing icon name resolves to Adwaita's missing-image SVG, the bundled
loader cannot decode it, and GTK aborts the whole process rather than
just skipping the icon — so the arrow is drawn with Cairo and the filter
box is a plain entry, not a GtkSearchEntry with its magnifier.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango

# Short enough that the list still fits BELOW the button. A popover that
# cannot fit is moved to the side by GTK, and a dropdown that opens
# sideways does not read as a dropdown.
MAX_LIST_HEIGHT = 240
MIN_WIDTH = 220


def draw_chevron(widget, cr):
    """The small triangle on a choice button."""
    width = widget.get_allocated_width()
    height = widget.get_allocated_height()
    colour = widget.get_style_context().get_color(Gtk.StateFlags.NORMAL)
    cr.set_source_rgba(colour.red, colour.green, colour.blue,
                       colour.alpha * 0.75)
    cr.move_to(width / 2 - 4, height / 2 - 2)
    cr.line_to(width / 2 + 4, height / 2 - 2)
    cr.line_to(width / 2, height / 2 + 3)
    cr.close_path()
    cr.fill()
    return False


def choice_button(options, current, on_change, searchable=False,
                  filter_placeholder=""):
    """A click-to-open, click-to-choose list.

    `options` is a list of (id, label); `on_change` is given the chosen
    id. Returns the button.
    """
    labels = dict(options)
    button = Gtk.MenuButton()
    button.get_style_context().add_class("choice")
    face = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    text = Gtk.Label(label=labels.get(current, ""), xalign=0)
    text.set_ellipsize(Pango.EllipsizeMode.END)
    face.pack_start(text, True, True, 0)
    chevron = Gtk.DrawingArea()
    chevron.set_size_request(10, 10)
    chevron.set_valign(Gtk.Align.CENTER)
    chevron.connect("draw", draw_chevron)
    face.pack_start(chevron, False, False, 0)
    button.add(face)

    popover = Gtk.Popover.new(button)
    popover.set_position(Gtk.PositionType.BOTTOM)
    popover.get_style_context().add_class("choice-popover")
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    for edge in ("top", "bottom", "start", "end"):
        getattr(column, "set_margin_" + edge)(6)

    search = None
    if searchable:
        search = Gtk.Entry()
        search.set_placeholder_text(filter_placeholder)
        column.pack_start(search, False, False, 0)

    listbox = Gtk.ListBox()
    listbox.get_style_context().add_class("choice-list")
    listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
    rows = {}
    for value, label in options:
        row = Gtk.ListBoxRow()
        item = Gtk.Label(label=label, xalign=0)
        for edge, size in (("top", 7), ("bottom", 7), ("start", 10),
                           ("end", 10)):
            getattr(item, "set_margin_" + edge)(size)
        row.add(item)
        row.choice_id = value
        row.choice_label = label.lower()
        listbox.add(row)
        rows[value] = row
    if current in rows:
        listbox.select_row(rows[current])

    def chosen(_listbox, row):
        if row is None:
            return
        text.set_text(labels.get(row.choice_id, ""))
        popover.popdown()
        on_change(row.choice_id)

    listbox.connect("row-activated", chosen)

    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_propagate_natural_height(True)
    scroller.set_max_content_height(MAX_LIST_HEIGHT)
    scroller.add(listbox)
    column.pack_start(scroller, True, True, 0)
    popover.add(column)

    if search is not None:
        def visible(row):
            needle = search.get_text().strip().lower()
            return needle in row.choice_label if needle else True

        listbox.set_filter_func(visible)
        search.connect("changed", lambda *_a: listbox.invalidate_filter())

        def first_match(*_args):
            for row in listbox.get_children():
                if visible(row):
                    chosen(listbox, row)
                    return
        search.connect("activate", first_match)

        def opened(*_args):
            search.set_text("")
            listbox.invalidate_filter()
            search.grab_focus()
        popover.connect("show", opened)

    # Match the button's width, so it opens as a dropdown rather than as
    # a panel of its own beside it.
    def match_width(*_args):
        width = button.get_allocated_width()
        if width > 1:
            popover.set_size_request(max(width, MIN_WIDTH), -1)
    button.connect("size-allocate", match_width)

    column.show_all()
    button.set_popover(popover)
    return button
