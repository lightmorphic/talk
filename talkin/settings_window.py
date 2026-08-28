"""The Settings window.

One Gtk.Window with every section from the original web settings page:
general, microphone, output/cleanup, personal dictionary, history, and
maintenance (restart/log/export/update).
Every change writes straight to disk the moment it's made - no Save
button, nothing to remember to click.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import os
import time
import zipfile

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango

from . import chooser, cleanup, i18n, tooltip, uninstall
from .config import ASSET_DIR, BASE_DIR, DATA_DIR, LOG_PATH, DEFAULTS
from .engine import MODEL_NAME, list_microphones
from . import session

log = logging.getLogger("talkin.settings")

_YELLOW = "#fbc711"

# The glyph size drawn inside every icon-only button (Gtk.IconSize.BUTTON
# is the same ~16px a normal GTK toolbar icon renders at) - the source
# PNGs are rasterized larger than this purely for HiDPI headroom.
_ICON_PX = 16

# One consistent gap between fields/rows/buttons everywhere in this
# window — about 4mm at a standard 96dpi display (~15.1px), rounded to
# the nearest value on the house style's 4px spacing scale.
_FIELD_GAP = 16

# The update-widget dot: Lightmorphic palette exactly, per house spec
# (do not substitute other greens/yellows/reds).
_DOT_SIZE = 18  # ~20% bigger than the original 15px - easier to see the
                # state (and the progress ring) actually change
_LM_SUCCESS = "#4bae4f"
_LM_WARNING = "#ffc006"
_LM_DANGER = "#f34236"
_LM_MUTED = "#a1a1aa"
_LM_ON_ACCENT = "#645007"
_LM_READY = "#2295f1"     # palette Blue: update downloaded, restart me
_LM_ON_READY = "#0a2a43"  # Blue's own contrast-checked on-accent
_LM_FG = "#fafafa"


def _hex_rgb(hexstr):
    h = hexstr.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

# The Lightmorphic style's dark tokens, translated to GTK CSS. This app
# commits to the brand's dark navy + yellow identity always (like the
# tray icons and overlay), rather than following the desktop's light/
# dark setting — there is no "light Talkin" any more than there's a
# grey tray icon.
_CSS = b"""
@define-color lm_bg #09090b;
@define-color lm_fg #fafafa;
@define-color lm_panel #1b1d29;
@define-color lm_panel_border alpha(#ffffff, 0.09);
@define-color lm_border #27272a;
@define-color lm_muted #1c1c1f;
@define-color lm_muted_fg #a1a1aa;
/* @lm_muted (#1c1c1f) sits almost exactly on top of @lm_panel
   (#1b1d29) - an icon button using it as its background was nearly
   invisible against the panel it's placed on. This is deliberately
   lighter than both so the button circle itself actually reads as a
   distinct, clickable shape, not just an icon floating on the panel. */
@define-color lm_icon_bg #42465f;
@define-color lm_icon_bg_hover #52566f;
@define-color lm_icon_border alpha(#ffffff, 0.14);
@define-color lm_accent #fbc711;
@define-color lm_accent_hover #ddaf0f;
@define-color lm_on_accent #645007;
@define-color lm_danger #f34236;
@define-color lm_danger_bg #350f0c;

window.talkin-settings {
  background-color: @lm_bg;
  color: @lm_fg;
  font-family: "Manrope", sans-serif;
}
.talkin-settings label, .talkin-settings check, .talkin-settings radio {
  color: @lm_fg;
}
.talkin-settings .section-title {
  font-weight: 600; font-size: 1.0625rem; color: @lm_fg;
}
.talkin-settings .hint { color: @lm_muted_fg; font-size: 0.8125rem; }
.talkin-settings .field-label { font-weight: 600; font-size: 0.9375rem; }

.talkin-settings .panel {
  background-color: @lm_panel;
  border: 1px solid @lm_panel_border;
  border-radius: 1.375rem;
  padding: 1.5rem;
  box-shadow: 0 2px 10px alpha(#000000, 0.35);
}

.talkin-settings button {
  border-radius: 0.875rem;
  padding: 6px 14px;
  box-shadow: none;
  -gtk-icon-shadow: none;
}
.talkin-settings button.icon-btn {
  min-width: 34px; min-height: 34px;
  padding: 0; margin: 0;
  border-radius: 50%;
  border: 1px solid @lm_icon_border;
  background-color: @lm_icon_bg;
  background-image: none;
  color: @lm_fg;
}
.talkin-settings button.icon-btn:hover { background-color: @lm_icon_bg_hover; }
.talkin-settings button.icon-btn.danger-armed {
  background-color: @lm_danger_bg;
  color: @lm_danger;
}
.talkin-settings button.primary {
  background-color: @lm_accent;
  background-image: none;
  color: @lm_on_accent;
  font-weight: 600;
  border: none;
  /* The theme puts a light shadow behind label text, which is meant for
     dark backgrounds. On yellow it shows as a pale halo around every
     letter. */
  text-shadow: none;
  -gtk-icon-shadow: none;
}
.talkin-settings button.primary:hover { background-color: @lm_accent_hover; }
.talkin-settings button.danger-armed {
  background-color: @lm_danger_bg;
  color: @lm_danger;
  border: 1px solid @lm_danger;
  font-weight: 600;
}

/* A plain ".talkin-settings label" rule would otherwise reach straight
   into these buttons' internal label widget and win over the color
   set above - a direct match always beats inherited color in GTK's
   CSS cascade, regardless of specificity or source order. */
.talkin-settings button.primary label { color: @lm_on_accent; text-shadow: none; }
.talkin-settings button.danger-armed label { color: @lm_danger; }

.talkin-settings entry, .talkin-settings treeview {
  border-radius: 0.875rem;
}

/* The click-to-open lists that replaced the comboboxes. */
.talkin-settings button.choice {
  background-color: @lm_icon_bg;
  background-image: none;
  border: 1px solid @lm_icon_border;
  color: @lm_fg;
  padding: 6px 12px;
  min-width: 190px;
}
.talkin-settings button.choice:hover { background-color: @lm_icon_bg_hover; }
list.choice-list { background-color: @lm_panel; }
list.choice-list row { border-radius: 0.625rem; }
list.choice-list row:selected {
  background-color: @lm_accent; color: @lm_on_accent; text-shadow: none;
}
list.choice-list row:selected label { color: @lm_on_accent; text-shadow: none; }
.talkin-settings treeview {
  background-color: @lm_muted;
  border: 1px solid @lm_border;
}
.talkin-settings treeview row {
  border-bottom: 1px solid @lm_panel_border;
  min-height: 2rem;
}
.talkin-settings treeview header button {
  background-color: @lm_bg;
  border: none;
  border-bottom: 1px solid @lm_border;
  padding: 8px 10px;
  font-weight: 600;
}
/* Selected-row colour is handled directly per-cell in Python
   (_style_selectable_row), not here - GTK's own :selected state on
   this system's theme rendered nearly-unreadable dark text no CSS
   override actually won against, so this is bypassed entirely rather
   than left in to (at best) do nothing or (at worst) fight it. */

.talkin-settings .category-list {
  background-color: @lm_bg;
  border-right: 1px solid @lm_panel_border;
  padding-top: 4px;
}
.talkin-settings .category-list row {
  background-color: transparent;
  color: @lm_muted_fg;
  border-left: 3px solid transparent;
}
.talkin-settings .category-list row label { color: @lm_muted_fg; }
.talkin-settings .category-list row:hover {
  background-color: alpha(#ffffff, 0.04);
}
.talkin-settings .category-list row:selected {
  background-color: alpha(#fbc711, 0.10);
  border-left: 3px solid @lm_accent;
}
.talkin-settings .category-list row:selected label {
  color: @lm_fg; font-weight: 600;
}

/* `outline` doesn't follow border-radius in GTK at all, on ANY
   widget - it always draws a plain rectangle, never the rounded ring
   it's meant to be. On round icon buttons and rounded capsules alike
   that reads as a stray box sitting outside the widget's own edge,
   which is what kept coming back here. box-shadow does follow
   border-radius, so it replaces outline everywhere focus is shown,
   not just on the couple of widgets it was visibly wrong on before. */
.talkin-settings *:focus {
  outline: none;
}
/* The ring goes on whole interactive widgets ONLY, never `*` - GTK
   propagates the FOCUSED state down into a widget's internal children,
   so a blanket *:focus draws a second ring floating in the middle of a
   composite control. */
.talkin-settings button:focus,
.talkin-settings entry:focus,
.talkin-settings checkbutton:focus {
  box-shadow: 0 0 0 2px @lm_accent;
}
/* Buttons that are already accent-colored need a focus ring that
   actually contrasts against them, not more of the same yellow. */
.talkin-settings button.primary:focus {
  box-shadow: 0 0 0 2px @lm_bg;
}
"""

_FONT_LOADED = False


def _load_bundled_font():
    """Register the bundled Manrope so it's usable by family name,
    without installing it system-wide (self-hosted, per house style)."""
    global _FONT_LOADED
    if _FONT_LOADED:
        return
    _FONT_LOADED = True
    path = os.path.join(ASSET_DIR, "fonts", "Manrope-VariableFont_wght.ttf")
    if not os.path.exists(path):
        return
    try:
        import ctypes
        fc = ctypes.CDLL("libfontconfig.so.1")
        fc.FcConfigAppFontAddFile(None, path.encode("utf-8"))
    except OSError:
        log.warning("could not register bundled font", exc_info=True)

_SECRET_KEYS = ("wayland_restore_token",)

# Limits for an imported dictionary; see the import handler for why.
_DICT_MAX_ENTRIES = 5000
_DICT_MAX_CHARS = 200


def _settings_without_secrets(path):
    """config.json as text, with anything private to this machine gone."""
    import json
    try:
        with open(path, "r", encoding="utf-8") as f:
            values = json.load(f)
    except (OSError, ValueError):
        return "{}\n"
    for key in _SECRET_KEYS:
        values.pop(key, None)
    return json.dumps(values, ensure_ascii=False, indent=2) + "\n"


class SettingsWindow(Gtk.Window):
    """Lazily built; call show_settings() to raise it, built once."""

    def __init__(self, app_obj):
        super().__init__(title=i18n.t("settings.title"))
        self.app_obj = app_obj
        self.config = app_obj.config
        self.dictionary = app_obj.dictionary
        self._switches = {}
        self.history = app_obj.history

        self.set_default_size(760, 560)
        self.get_style_context().add_class("talkin-settings")
        _load_bundled_font()
        self._apply_css()
        # Popup windows do not always inherit this window's CSS
        # ancestry, so a scoped rule cannot reach them. This is the one
        # thing that does: telling GTK itself to
        # prefer its dark theme variant application-wide, so the popup
        # inherits *a* coherent dark palette instead of the light
        # default, even though it can't inherit the exact house colours.
        Gtk.Settings.get_default().set_property(
            "gtk-application-prefer-dark-theme", True)
        try:
            self.set_icon_from_file(os.path.join(ASSET_DIR, "talkin.png"))
        except GLib.GError:
            log.warning("could not load settings window icon", exc_info=True)
        self.connect("delete-event", self._on_close)


        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)

        header = self._build_header()
        header.set_margin_top(20)
        header.set_margin_bottom(4)
        header.set_margin_start(24)
        header.set_margin_end(24)
        outer.pack_start(header, False, False, 0)

        # A normal two-pane settings layout: a category list on the
        # left, one page visible at a time on the right — not one long
        # page stacking every section, which is what forced scrolling
        # through everything just to reach Maintenance.
        split = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        split.set_margin_top(12)
        outer.pack_start(split, True, True, 0)

        categories = [
            ("general", "settings.section.general", self._build_general),
            ("microphone", "settings.section.microphone",
             self._build_microphone),
            ("output", "settings.section.output", self._build_output),
            ("dictionary", "settings.section.dictionary",
             self._build_dictionary),
            ("history", "settings.section.history", self._build_history),
            ("maintenance", "settings.section.maintenance",
             self._build_maintenance),
            ("help", "settings.section.help", self._build_help),
        ]

        sidebar = Gtk.ListBox()
        sidebar.get_style_context().add_class("category-list")
        sidebar.set_size_request(180, -1)
        sidebar.set_selection_mode(Gtk.SelectionMode.SINGLE)

        self._stack = Gtk.Stack()
        self._stack.set_hexpand(True)
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(120)

        for key, title_key, builder in categories:
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=i18n.t(title_key), xalign=0)
            label.set_margin_top(10)
            label.set_margin_bottom(10)
            label.set_margin_start(16)
            label.set_margin_end(16)
            row.add(label)
            row.category_key = key
            sidebar.add(row)

            page_scroller = Gtk.ScrolledWindow()
            page_scroller.set_policy(
                Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            content.set_margin_top(4)
            content.set_margin_bottom(20)
            content.set_margin_start(20)
            content.set_margin_end(24)
            content.pack_start(builder(), False, False, 0)
            page_scroller.add(content)
            self._stack.add_named(page_scroller, key)

        self._sidebar = sidebar
        sidebar.connect("row-selected", self._on_category_selected)
        split.pack_start(sidebar, False, False, 0)
        split.pack_start(self._stack, True, True, 0)

        sidebar.select_row(sidebar.get_row_at_index(0))
        self._refresh_dictionary()
        self._refresh_history()

    def current_page(self):
        """Which section is showing, so a rebuild can return to it."""
        return self._stack.get_visible_child_name()

    def show_page(self, key):
        row = self._row_for_page(key)
        if row is not None:
            self._sidebar.select_row(row)

    def _row_for_page(self, key):
        for row in self._sidebar.get_children():
            if getattr(row, "category_key", None) == key:
                return row
        return None

    def _on_category_selected(self, _listbox, row):
        if row is not None:
            self._stack.set_visible_child_name(row.category_key)

    def _apply_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    # -- small builders ------------------------------------------------

    def _section(self, title_key, hint_key=None):
        """A panel: the one surface everything in a section lives in."""
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=_FIELD_GAP)
        panel.get_style_context().add_class("panel")
        title = Gtk.Label(label=i18n.t(title_key), xalign=0)
        title.get_style_context().add_class("section-title")
        panel.pack_start(title, False, False, 0)
        if hint_key:
            hint = Gtk.Label(label=i18n.t(hint_key), xalign=0, wrap=True)
            hint.get_style_context().add_class("hint")
            panel.pack_start(hint, False, False, 0)
        return panel

    def _row(self, label_text, widget):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=_FIELD_GAP)
        label = Gtk.Label(label=label_text, xalign=0)
        label.get_style_context().add_class("field-label")
        label.set_size_request(180, -1)
        row.pack_start(label, False, False, 0)
        row.pack_start(widget, True, True, 0)
        return row

    def _style_selectable_row(self, tree, renderers, text_renderers=None):
        """Explicit foreground/background for treeview cells, selected
        or not, bypassing GTK's theme-driven colours entirely.

        Every cell's text colour is forced, not just the selected
        row's: CellRendererText takes its default colour from the
        active system theme, NOT from any of this window's own CSS -
        so leaving unselected rows themed made them render dark-on-dark
        on the very theme this app runs on (the earlier version of this
        helper only fixed the selected row and left that hole open).
        Same "take direct control instead of fighting the platform"
        approach as the custom tooltip popup.

        text_renderers (defaults to all of `renderers`) is the subset
        whose text colour this owns - a renderer with its own
        intentional foreground (the dictionary's yellow "remove"
        column) is left alone entirely."""
        selection = tree.get_selection()
        accent_r, accent_g, accent_b = _hex_rgb(_YELLOW)
        fg_r, fg_g, fg_b = _hex_rgb(_LM_FG)
        touch_fg = set(text_renderers) if text_renderers is not None \
            else set(renderers)

        def make_painter(renderer):
            def paint(_column, r, _model, it, _data):
                if renderer in touch_fg:
                    r.set_property(
                        "foreground-rgba", Gdk.RGBA(fg_r, fg_g, fg_b, 1.0))
                if selection.iter_is_selected(it):
                    r.set_property(
                        "cell-background-rgba",
                        Gdk.RGBA(accent_r, accent_g, accent_b, 0.22))
                else:
                    r.set_property("cell-background-set", False)
            return paint

        for column, renderer in zip(tree.get_columns(), renderers):
            column.set_cell_data_func(renderer, make_painter(renderer))
        selection.connect("changed", lambda _s: tree.queue_draw())

    def _icon_button(self, icon_name, tooltip_text):
        """A circular, icon-only action button — Charlie's house style
        for secondary actions (matches the round-icon-row convention
        used across his other apps' toolbars).

        icon_name loads assets/icons/<icon_name>.png, bundled with the
        app, rather than a theme icon name — the Lightmorphic house
        style calls for self-contained icons, not ones whose glyph (or
        even existence) depends on whatever icon theme happens to be
        installed on the host system. PNG, not SVG, for the same reason
        the window icon is PNG: SVG loads through a separate gdk-pixbuf/
        librsvg plugin that isn't reliably bundled in the AppImage (the
        .svg files alongside these are the editable source; only the
        rasterized .png is ever loaded at runtime).

        The source PNGs are rasterized at 64x64 for headroom on HiDPI
        displays - loading them with plain new_from_file() rendered
        them at that full native size instead of a normal small icon,
        which is what made every button huge. Scaling explicitly down
        to _ICON_PX here is what actually makes that headroom useful
        instead of just oversized."""
        icon_path = os.path.join(ASSET_DIR, "icons", icon_name + ".png")
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            icon_path, _ICON_PX, _ICON_PX, True)
        image = Gtk.Image.new_from_pixbuf(pixbuf)
        button = Gtk.Button()
        button.set_image(image)
        # Some GTK themes hide button images by default unless told
        # otherwise - these buttons have no label, so without this an
        # icon-only button could render completely blank.
        button.set_always_show_image(True)
        button.get_style_context().add_class("icon-btn")
        tooltip.attach(button, tooltip_text)
        # Packed alone (not in a horizontal row) into a vertical box, a
        # widget defaults to Align.FILL on the cross axis and stretches
        # to the panel's full width - pinning halign here means every
        # icon button stays a compact circle regardless of what kind of
        # container it ends up in.
        button.set_halign(Gtk.Align.START)
        return button

    def _arm_destructive(self, button, action, armed_tooltip=None):
        """A destructive action never fires on one click: the button
        turns red and asks again in place, reverting after a few
        seconds — never a confirm() dialog. Works for both labelled
        buttons (swaps the label) and icon-only ones (swaps the
        tooltip instead, since there's no label to change)."""
        original_label = button.get_label()
        original_tooltip = tooltip.get_text(button)
        state = {"armed": False, "timeout": None}

        def revert():
            state["armed"] = False
            state["timeout"] = None
            if original_label is not None:
                button.set_label(original_label)
            tooltip.attach(button, original_tooltip)
            button.get_style_context().remove_class("danger-armed")
            return False

        def on_click(_btn):
            if state["armed"]:
                if state["timeout"] is not None:
                    GLib.source_remove(state["timeout"])
                revert()
                action()
                return
            state["armed"] = True
            if original_label is not None:
                button.set_label(original_label + "?")
            tooltip.attach(
                button, armed_tooltip or ((original_tooltip or "") + "?"))
            button.get_style_context().add_class("danger-armed")
            state["timeout"] = GLib.timeout_add_seconds(4, revert)

        button.connect("clicked", on_click)

    def _on_uninstall(self):
        """Remove every trace of Talkin, then quit."""
        problems = uninstall.run()
        if problems:
            log.warning("uninstall left things behind: %s", problems)
        self.app_obj.notify(i18n.t("settings.uninstall_done"))
        GLib.timeout_add_seconds(2, lambda: (self.app_obj.quit(), False)[1])


    def _switch_row(self, key, label_key, hint_key=None, on_change=None):
        """A labelled toggle bound to a config key.

        Switches, not tick boxes: a switch says on or off at a glance,
        which suits settings that change how the app behaves. Every
        switch built for the same key is remembered, so the same setting
        shown on two pages stays in step instead of one going stale.
        """
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=_FIELD_GAP)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label=i18n.t(label_key), xalign=0)
        title.get_style_context().add_class("field-label")
        labels.pack_start(title, False, False, 0)
        if hint_key:
            hint = Gtk.Label(label=i18n.t(hint_key), xalign=0, wrap=True)
            hint.get_style_context().add_class("hint")
            labels.pack_start(hint, False, False, 0)
        row.pack_start(labels, True, True, 0)

        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.START)
        switch.set_active(bool(self.config.get(key)))
        switch.connect("notify::active", self._on_switch, key, on_change)
        self._switches.setdefault(key, []).append(switch)
        row.pack_start(switch, False, False, 0)
        return row

    def _on_switch(self, switch, _param, key, on_change):
        value = switch.get_active()
        if bool(self.config.get(key)) == value:
            return   # echo from syncing the twin; nothing to do
        self._set(key, value)
        for other in self._switches.get(key, []):
            if other is not switch and other.get_active() != value:
                other.set_active(value)
        if on_change is not None:
            on_change(value)

    def _get(self, key):
        return self.config.get(key)

    def _set(self, key, value):
        # No separate save step: every change lands on disk the moment
        # it is made, same as ticking a checkbox in any normal settings
        # app.
        self.config.update({key: value})
        if key == "autostart":
            from .config import set_autostart
            set_autostart(value)
        if key == "float_button":
            # Show or hide it now rather than at next launch: a setting
            # that appears to do nothing until a restart reads as broken.
            app = self.app_obj
            if value and app.float_button is None:
                from .float_button import FloatButton
                app.float_button = FloatButton(
                    on_toggle=app._toggle, on_correction=app._correction)
                app.float_button.set_state(app.state)
            elif not value and app.float_button is not None:
                app.float_button.stop()
                app.float_button = None
        if key == "sounds" and value:
            # Play it on the spot, so the switch says what it does
            # instead of describing it.
            from . import sounds
            sounds.play("start")
        self.app_obj.apply_settings()

    # -- header ----------------------------------------------------------

    def _build_header(self):
        from . import __version__
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=_FIELD_GAP)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label=i18n.t("settings.title"), xalign=0)
        title.get_style_context().add_class("section-title")
        left.pack_start(title, False, False, 0)
        sub = Gtk.Label(label=i18n.t("settings.subtitle"), xalign=0, wrap=True)
        sub.get_style_context().add_class("hint")
        left.pack_start(sub, False, False, 0)
        row.pack_start(left, True, True, 0)

        # Version + status dot, right-aligned like the same pairing in
        # Charlie's other apps (Fetch Terminal etc.) rather than buried
        # left-aligned under the title.
        ver_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        ver_row.set_halign(Gtk.Align.END)
        ver_row.set_valign(Gtk.Align.START)

        ver_text_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                               spacing=5)
        name_label = Gtk.Label(label="Talkin")
        name_label.get_style_context().add_class("hint")
        ver_text_row.pack_start(name_label, False, False, 0)
        ver_num_label = Gtk.Label(label="v{}".format(__version__))
        ver_num_label.get_style_context().add_class("hint")
        ver_text_row.pack_start(ver_num_label, False, False, 0)

        ver_event = Gtk.EventBox()
        ver_event.add(ver_text_row)
        tooltip.attach(ver_event, "talkin.lightmorphic.com")
        ver_event.connect("button-press-event", self._on_version_clicked)
        ver_event.connect("realize", lambda w: w.get_window().set_cursor(
            Gdk.Cursor.new_from_name(w.get_display(), "pointer")))
        ver_row.pack_start(ver_event, False, False, 0)

        # The dot per Charlie's house update-widget spec: a small
        # custom-drawn circle carrying its own state via colour, a
        # hollow progress ring while downloading, and an overlay icon
        # for the two clickable states — no separate button, no
        # banner, no dialog. The dot IS the whole update UI.
        self._download_fraction = 0.0
        self._update_dot = Gtk.DrawingArea()
        self._update_dot.set_size_request(_DOT_SIZE, _DOT_SIZE)
        self._update_dot.connect("draw", self._draw_update_dot)
        dot_event = Gtk.EventBox()
        dot_event.add(self._update_dot)
        dot_event.connect("button-press-event", self._on_update_dot_clicked)
        ver_row.pack_start(dot_event, False, False, 0)
        row.pack_start(ver_row, False, False, 0)

        self._update_state = "checking"
        self._pulse = 0.0
        self._pulse_timer = None
        self._update_tag = None
        self._set_update_dot("checking", i18n.t("update.checking"))
        GLib.idle_add(self._check_update)
        return row

    def _on_version_clicked(self, _widget, _event):
        import webbrowser
        webbrowser.open("https://talkin.lightmorphic.com")

    _PULSE_MS = 40
    _PULSE_BEATS = 3          # how many times it breathes before settling
    _PULSE_PERIOD = 0.6       # seconds per beat

    def _set_update_dot(self, state, tooltip_text):
        self._update_state = state
        tooltip.attach(self._update_dot, tooltip_text)
        self._sync_pulse()
        self._update_dot.queue_draw()

    def _sync_pulse(self):
        """Breathe while checking; hold still otherwise.

        A dot that simply sat there grey said nothing about whether the
        check had started. Pulsing, rather than blinking: a blink reads
        as a fault, a slow swell reads as work in progress.
        """
        want = self._update_state == "checking"
        if want and self._pulse_timer is None:
            self._pulse = 0.0
            self._pulse_timer = GLib.timeout_add(self._PULSE_MS, self._beat)
        elif not want and self._pulse_timer is not None:
            GLib.source_remove(self._pulse_timer)
            self._pulse_timer = None
            self._pulse = 0.0
            self._update_dot.queue_draw()

    def _beat(self):
        self._pulse += self._PULSE_MS / 1000.0
        self._update_dot.queue_draw()
        # Three beats and then steady, even if the answer is slow: an
        # animation that never stops stops meaning anything.
        if self._pulse > self._PULSE_BEATS * self._PULSE_PERIOD:
            self._pulse_timer = None
            return False
        return True

    def _draw_update_dot(self, widget, cr):
        import math
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        cx, cy = w / 2, h / 2
        r = min(w, h) / 2 - 1
        state = self._update_state

        if state == "downloading":
            cr.set_line_width(2.0)
            cr.set_source_rgba(0.63, 0.63, 0.67, 0.35)
            cr.arc(cx, cy, r - 1, 0, 2 * math.pi)
            cr.stroke()
            cr.set_source_rgb(*_hex_rgb(_LM_WARNING))
            start = -math.pi / 2
            end = start + 2 * math.pi * max(0.02, self._download_fraction)
            cr.arc(cx, cy, r - 1, start, end)
            cr.stroke()
            return False

        # Plain filled circles: the colour carries the whole meaning,
        # and a glyph drawn inside something this small is noise rather
        # than information. Ready is palette Blue rather than a second
        # green, so "downloaded, click to restart" and "you are up to
        # date" are told apart by colour alone.
        color = {
            "checking": _LM_MUTED, "uptodate": _LM_SUCCESS,
            "available": _LM_WARNING, "ready": _LM_READY,
            "error": _LM_DANGER,
        }.get(state, _LM_MUTED)
        if state == "checking" and self._pulse_timer is not None:
            swell = 0.5 - 0.5 * math.cos(
                2 * math.pi * self._pulse / self._PULSE_PERIOD)
            cr.set_source_rgba(*_hex_rgb(color), 0.30 + 0.70 * swell)
            cr.arc(cx, cy, r * (0.72 + 0.28 * swell), 0, 2 * math.pi)
            cr.fill()
            return False

        cr.set_source_rgb(*_hex_rgb(color))
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.fill()

        return False

    def _build_general(self):
        box = self._section("settings.section.general")

        def on_lang(chosen):
            if chosen == self.config.get("language"):
                return
            self._set("language", chosen)
            # Redraw everything in the new language now. Waiting for a
            # restart makes the setting look broken, which is exactly
            # how it looked.
            GLib.idle_add(self.app_obj.retranslate)

        lang_button = chooser.choice_button(
            i18n.available_languages(), self.config.get("language"), on_lang,
            searchable=True,
            filter_placeholder=i18n.t("settings.filter"))
        box.pack_start(self._row(i18n.t("settings.language"), lang_button),
                       False, False, 0)

        box.pack_start(self._switch_row("autostart", "settings.autostart"),
                       False, False, 0)

        # The floating button is the click route: no shortcut signal to
        # arrive late or early, so it works where the keyboard does not.
        box.pack_start(
            self._switch_row("float_button", "settings.float_button"),
            False, False, 0)

        # While dictating you are looking at the document, not at the
        # button, so a sound is the only cue that reaches you.
        box.pack_start(
            self._switch_row("sounds", "settings.sounds",
                             "settings.sounds_help"),
            False, False, 0)

        # The same switch appears on the History page. Someone thinking
        # about what is kept will look there; someone scanning settings
        # will look here. Both are legitimate, so it is in both places.
        box.pack_start(
            self._switch_row("history_enabled", "settings.history_enabled",
                             "settings.history_enabled_help"),
            False, False, 0)

        return box

    # -- microphone ----------------------------------------------------

    def _build_microphone(self):
        box = self._section("settings.section.microphone")
        mics = [(mic_id, i18n.t("settings.mic.default")
                 if mic_id == "default" else name)
                for mic_id, name in list_microphones()]
        current = self.config.get("mic")
        if current not in dict(mics) and mics:
            current = mics[0][0]
        self._mic_button = chooser.choice_button(
            mics, current, lambda value: self._set("mic", value))
        box.pack_start(self._row(i18n.t("settings.mic"), self._mic_button),
                       False, False, 0)

        test_btn = self._icon_button(
            "audio-input-microphone-symbolic", i18n.t("settings.mic_test"))
        test_btn.connect("clicked", self._on_mic_test)
        test_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        test_row.pack_start(test_btn, False, False, 0)
        test_label = Gtk.Label(label=i18n.t("settings.mic_test_label"),
                               xalign=0)
        test_row.pack_start(test_label, False, False, 0)
        box.pack_start(test_row, False, False, 0)

        self._mic_result = Gtk.Label(xalign=0, wrap=True)
        self._mic_result.get_style_context().add_class("hint")
        box.pack_start(self._mic_result, False, False, 0)
        return box

    def _on_mic_test(self, button):
        if self.app_obj.state != "idle":
            self._mic_result.set_text(i18n.t("error.mic"))
            return
        button.set_sensitive(False)
        self._mic_result.set_text(i18n.t("settings.mic_testing"))

        def run():
            try:
                self.app_obj.recorder.start()
                time.sleep(3)
                audio = self.app_obj.recorder.stop()
            except Exception:
                log.exception("mic test failed")
                GLib.idle_add(self._mic_test_done, button, None, None)
                return
            peak = float(abs(audio).max()) if len(audio) else 0.0
            text = ""
            if peak > 0.01 and self.app_obj.transcriber.ready:
                import threading
                done = threading.Event()
                out = {}

                def collect(t, err):
                    out["text"] = t or ""
                    done.set()
                self.app_obj.transcriber.submit(audio, collect)
                done.wait(timeout=30)
                text = cleanup.clean(
                    out.get("text", ""), self.config, self.dictionary)
            GLib.idle_add(self._mic_test_done, button, peak, text)

        import threading
        threading.Thread(target=run, daemon=True).start()

    def _mic_test_done(self, button, peak, text):
        button.set_sensitive(True)
        if peak is None:
            self._mic_result.set_text(i18n.t("error.mic"))
        elif peak <= 0.01:
            self._mic_result.set_text(i18n.t("settings.mic_test_nothing"))
        else:
            parts = ["{}: {:.2f}".format(
                i18n.t("settings.mic_test_level"), peak)]
            if text:
                parts.append('{}: "{}"'.format(
                    i18n.t("settings.mic_test_heard"), text))
            self._mic_result.set_text("  ·  ".join(parts))
        return False

    # -- output / cleanup ----------------------------------------------

    def _build_output(self):
        box = self._section("settings.section.output")
        injection = chooser.choice_button(
            [("paste", i18n.t("settings.injection.paste")),
             ("type", i18n.t("settings.injection.type"))],
            self.config.get("injection"),
            lambda value: self._set("injection", value))
        box.pack_start(self._row(i18n.t("settings.injection"), injection),
                       False, False, 0)

        cleanup_title = Gtk.Label(label=i18n.t("settings.section.cleanup"),
                                  xalign=0)
        cleanup_title.get_style_context().add_class("section-title")
        box.pack_start(cleanup_title, False, False, 0)

        box.pack_start(
            self._switch_row("cleanup_fillers", "settings.cleanup_fillers"),
            False, False, 0)
        box.pack_start(
            self._switch_row("cleanup_dictionary",
                             "settings.cleanup_dictionary"),
            False, False, 0)
        return box

    # -- dictionary ------------------------------------------------------

    def _build_dictionary(self):
        box = self._section("settings.section.dictionary",
                            "settings.dictionary_help")

        self._dict_store = Gtk.ListStore(str, str)
        tree = Gtk.TreeView(model=self._dict_store)
        # Same reasoning as the history treeview: mouse-driven (click
        # the "remove" cell to delete a row), not keyboard-navigated -
        # skip GTK's native per-cell focus indicator entirely.
        tree.set_can_focus(False)
        heard_renderer = Gtk.CellRendererText(xpad=10, ypad=6)
        tree.append_column(Gtk.TreeViewColumn(
            i18n.t("settings.dict.heard"), heard_renderer, text=0))
        say_renderer = Gtk.CellRendererText(xpad=10, ypad=6)
        tree.append_column(Gtk.TreeViewColumn(
            i18n.t("settings.dict.say"), say_renderer, text=1))
        remove_renderer = Gtk.CellRendererText(
            text=i18n.t("settings.dict.remove"), foreground=_YELLOW,
            xpad=10, ypad=6)
        remove_col = Gtk.TreeViewColumn("", remove_renderer)
        tree.append_column(remove_col)
        tree.connect("row-activated", self._on_dict_row_activated)
        self._style_selectable_row(
            tree, [heard_renderer, say_renderer, remove_renderer],
            text_renderers=[heard_renderer, say_renderer])

        self._dict_scroller = Gtk.ScrolledWindow()
        self._dict_scroller.set_min_content_height(140)
        self._dict_scroller.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._dict_scroller.set_no_show_all(True)
        self._dict_scroller.add(tree)
        # no_show_all on the scroller means the parent window's
        # show_all() never cascades into it OR its children at all -
        # explicitly showing the treeview itself is unaffected by that
        # flag (it only governs automatic cascading from an ancestor).
        tree.show()
        box.pack_start(self._dict_scroller, False, False, 0)

        self._dict_empty = Gtk.Label(label=i18n.t("settings.dict.empty"),
                                     xalign=0, wrap=True)
        self._dict_empty.get_style_context().add_class("hint")
        self._dict_empty.set_no_show_all(True)
        box.pack_start(self._dict_empty, False, False, 0)

        entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                            spacing=_FIELD_GAP)
        self._dict_heard = Gtk.Entry(
            placeholder_text=i18n.t("settings.dict.heard"))
        self._dict_say = Gtk.Entry(
            placeholder_text=i18n.t("settings.dict.say"))
        entry_row.pack_start(self._dict_heard, True, True, 0)
        entry_row.pack_start(self._dict_say, True, True, 0)
        box.pack_start(entry_row, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=_FIELD_GAP)
        add_btn = self._icon_button(
            "list-add-symbolic", i18n.t("settings.dict.add"))
        add_btn.connect("clicked", self._on_dict_add)
        actions.pack_start(add_btn, False, False, 0)
        export_btn = self._icon_button(
            "document-save-symbolic", i18n.t("settings.dict.export"))
        export_btn.connect("clicked", self._on_dict_export)
        actions.pack_start(export_btn, False, False, 0)
        import_btn = self._icon_button(
            "document-open-symbolic", i18n.t("settings.dict.import"))
        import_btn.connect("clicked", self._on_dict_import)
        actions.pack_start(import_btn, False, False, 0)
        box.pack_start(actions, False, False, 0)
        return box

    def _refresh_dictionary(self):
        self._dict_store.clear()
        entries = self.dictionary.entries()
        for e in entries:
            self._dict_store.append([e["heard"], e["say"]])
        self._dict_scroller.set_visible(bool(entries))
        self._dict_empty.set_visible(not entries)

    def _on_dict_row_activated(self, tree, path, column):
        if column.get_title() != "":
            return
        heard = self._dict_store[path][0]
        self.dictionary.remove(heard)
        self._refresh_dictionary()

    def _on_dict_add(self, _button):
        heard = self._dict_heard.get_text().strip()
        say = self._dict_say.get_text().strip()
        if not heard or not say:
            return
        self.dictionary.add(heard, say)
        self._dict_heard.set_text("")
        self._dict_say.set_text("")
        self._refresh_dictionary()

    def _on_dict_export(self, _button):
        import json
        dialog = Gtk.FileChooserDialog(
            title=i18n.t("settings.dict.export"), parent=self,
            action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons(
            i18n.t("correction.cancel"), Gtk.ResponseType.CANCEL,
            i18n.t("settings.dict.export"), Gtk.ResponseType.OK)
        dialog.set_current_name("talkin-dictionary.json")
        if dialog.run() == Gtk.ResponseType.OK:
            payload = json.dumps(
                {"talkin_dictionary": 1, "entries": self.dictionary.entries()},
                ensure_ascii=False, indent=2)
            with open(dialog.get_filename(), "w", encoding="utf-8") as f:
                f.write(payload)
        dialog.destroy()

    def _on_dict_import(self, _button):
        import json
        dialog = Gtk.FileChooserDialog(
            title=i18n.t("settings.dict.import"), parent=self,
            action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons(
            i18n.t("correction.cancel"), Gtk.ResponseType.CANCEL,
            i18n.t("settings.dict.import"), Gtk.ResponseType.OK)
        f = Gtk.FileFilter()
        f.add_pattern("*.json")
        f.set_name("JSON")
        dialog.add_filter(f)
        if dialog.run() == Gtk.ResponseType.OK:
            try:
                with open(dialog.get_filename(), "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                data = {}
            if data.get("talkin_dictionary") == 1 and \
                    isinstance(data.get("entries"), list):
                merged = {e["heard"].lower(): e
                          for e in self.dictionary.entries()}
                # Bounded on purpose. An imported file is the only thing
                # here that comes from outside, every entry becomes a
                # regular expression run against every transcript, and
                # each replacement is text this app will type into
                # whatever window has focus. A dictionary of a hundred
                # thousand entries, or one holding a page of text, is
                # not a dictionary.
                for e in data["entries"][:_DICT_MAX_ENTRIES]:
                    if not isinstance(e, dict):
                        continue
                    heard = str(e.get("heard", "")).strip()[:_DICT_MAX_CHARS]
                    say = str(e.get("say", "")).strip()[:_DICT_MAX_CHARS]
                    if heard and say:
                        merged[heard.lower()] = {"heard": heard, "say": say}
                self.dictionary.replace_all(list(merged.values()))
                self._refresh_dictionary()
                self.app_obj.notify(i18n.t("settings.dict.imported"))
            else:
                self.app_obj.notify(i18n.t("settings.dict.import_bad"))
        dialog.destroy()

    # -- history -----------------------------------------------------

    def _build_history(self):
        box = self._section("settings.section.history",
                            "settings.history_help")

        # The switch belongs here, not buried on the General page: this
        # is where someone goes when they think about what is being kept.
        box.pack_start(
            self._switch_row("history_enabled", "settings.history_enabled",
                             "settings.history_enabled_help"),
            False, False, 0)

        self._history_store = Gtk.ListStore(str, str)
        tree = Gtk.TreeView(model=self._history_store)
        # This is a mouse-driven, read-mostly list, not something meant
        # for keyboard navigation - leaving it focusable only added a
        # second, native "focused cell" indicator GTK draws internally
        # (via gtk_render_focus(), entirely separate from and not
        # reachable through the CSS box-shadow focus ring above) right
        # on top of the selection highlight, reading as yet another
        # stray line rather than useful affordance.
        tree.set_can_focus(False)
        # Both columns are untitled, so the header row was just two
        # empty dark blobs sitting above the list - dead chrome.
        tree.set_headers_visible(False)
        # xpad/ypad give every cell real breathing room instead of text
        # sitting flush against the row/column edge - the date column
        # gets extra right-padding specifically so it reads as its own
        # column instead of running straight into the text column.
        when_renderer = Gtk.CellRendererText(xpad=10, ypad=6)
        when_col = Gtk.TreeViewColumn("", when_renderer, text=0)
        tree.append_column(when_col)
        text_renderer = Gtk.CellRendererText(
            wrap_width=360, wrap_mode=Pango.WrapMode.WORD_CHAR,
            xpad=10, ypad=6)
        tree.append_column(Gtk.TreeViewColumn("", text_renderer, text=1))
        self._style_selectable_row(tree, [when_renderer, text_renderer])

        self._history_scroller = Gtk.ScrolledWindow()
        self._history_scroller.set_min_content_height(160)
        self._history_scroller.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._history_scroller.set_no_show_all(True)
        self._history_scroller.add(tree)
        tree.show()
        box.pack_start(self._history_scroller, False, False, 0)

        self._history_empty = Gtk.Label(
            label=i18n.t("settings.history.empty"), xalign=0)
        self._history_empty.get_style_context().add_class("hint")
        self._history_empty.set_no_show_all(True)
        box.pack_start(self._history_empty, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=_FIELD_GAP)
        export_btn = self._icon_button(
            "document-save-symbolic", i18n.t("settings.history.export"))
        export_btn.connect("clicked", self._on_history_export)
        actions.pack_start(export_btn, False, False, 0)
        clear_btn = self._icon_button(
            "user-trash-symbolic", i18n.t("settings.history.clear"))
        self._arm_destructive(clear_btn, self._on_history_clear)
        actions.pack_start(clear_btn, False, False, 0)
        box.pack_start(actions, False, False, 0)
        return box

    def _refresh_history(self):
        self._history_store.clear()
        entries = self.history.entries(limit=100)
        for e in entries:
            when = time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(e["ts"]))
            self._history_store.append([when, e.get("clean", "")])
        self._history_scroller.set_visible(bool(entries))
        self._history_empty.set_visible(not entries)

    def _on_history_export(self, _button):
        dialog = Gtk.FileChooserDialog(
            title=i18n.t("settings.history.export"), parent=self,
            action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons(
            i18n.t("correction.cancel"), Gtk.ResponseType.CANCEL,
            i18n.t("settings.history.export"), Gtk.ResponseType.OK)
        dialog.set_current_name("talkin-history.txt")
        if dialog.run() == Gtk.ResponseType.OK:
            lines = ["{}\t{}".format(
                time.strftime("%Y-%m-%d %H:%M", time.localtime(e["ts"])),
                e.get("clean", "")) for e in self.history.entries(limit=100000)]
            with open(dialog.get_filename(), "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        dialog.destroy()

    def _on_history_clear(self):
        self.history.clear()
        self._refresh_history()
        self.app_obj.notify(i18n.t("settings.history.cleared"))

    # -- maintenance / update -------------------------------------------

    # The dot's five states, in the order they happen to you.
    _DOT_STATES = (
        ("checking", _LM_MUTED, "help.dot.checking"),
        ("uptodate", _LM_SUCCESS, "help.dot.uptodate"),
        ("available", _LM_WARNING, "help.dot.available"),
        ("ready", _LM_READY, "help.dot.ready"),
        ("error", _LM_DANGER, "help.dot.error"),
    )

    def _build_help(self):
        """What this is, how to work it, and what the colours mean.

        The update dot is the whole update interface, so its colours are
        the one thing here that genuinely needs explaining — and they are
        explained with the actual dots, drawn the same way, rather than
        with the names of colours.
        """
        box = self._section("settings.section.help", "help.intro")

        box.pack_start(self._help_block("help.using_title", "help.using"),
                       False, False, 0)
        box.pack_start(self._help_block("help.updates_title", "help.updates"),
                       False, False, 0)

        for _state, colour, text_key in self._DOT_STATES:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            swatch = Gtk.DrawingArea()
            swatch.set_size_request(_DOT_SIZE, _DOT_SIZE)
            swatch.set_valign(Gtk.Align.START)
            swatch.set_margin_top(3)
            swatch.connect("draw", self._draw_swatch, colour)
            row.pack_start(swatch, False, False, 0)
            label = Gtk.Label(label=i18n.t(text_key), xalign=0, wrap=True)
            label.get_style_context().add_class("hint")
            row.pack_start(label, True, True, 0)
            box.pack_start(row, False, False, 0)

        box.pack_start(self._help_block("help.privacy_title", "help.privacy"),
                       False, False, 0)
        return box

    def _help_block(self, title_key, body_key):
        block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        title = Gtk.Label(label=i18n.t(title_key), xalign=0)
        title.get_style_context().add_class("section-title")
        block.pack_start(title, False, False, 0)
        body = Gtk.Label(label=i18n.t(body_key), xalign=0, wrap=True)
        body.get_style_context().add_class("hint")
        block.pack_start(body, False, False, 0)
        return block

    @staticmethod
    def _draw_swatch(widget, cr, colour):
        import math
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        radius = min(width, height) / 2 - 1
        cr.set_source_rgb(*_hex_rgb(colour))
        cr.arc(width / 2, height / 2, radius, 0, 2 * math.pi)
        cr.fill()
        return False

    def _build_maintenance(self):
        box = self._section("settings.section.maintenance",
                            "settings.maintenance_help")

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=_FIELD_GAP)
        restart_btn = self._icon_button(
            "view-refresh-symbolic", i18n.t("settings.restart"))
        restart_btn.connect(
            "clicked", lambda *_r: self.app_obj.restart())
        actions.pack_start(restart_btn, False, False, 0)

        log_btn = self._icon_button(
            "text-x-generic-symbolic", i18n.t("settings.view_log"))
        log_btn.connect("clicked", self._on_view_log)
        actions.pack_start(log_btn, False, False, 0)

        export_btn = self._icon_button(
            "package-x-generic-symbolic", i18n.t("settings.export_all"))
        export_btn.connect("clicked", self._on_export_all)
        actions.pack_start(export_btn, False, False, 0)
        box.pack_start(actions, False, False, 0)

        # Deleting the AppImage leaves the 600 MB model, the settings and
        # the menu entry behind, so "removed" does not mean removed. This
        # is the only thing that can honestly take it all away.
        removable = uninstall.total_bytes()
        uninstall_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        uninstall_label = Gtk.Label(
            label=i18n.t("settings.uninstall_help").format(
                mb=int(removable / (1024 * 1024))),
            xalign=0, wrap=True)
        uninstall_label.get_style_context().add_class("hint")
        uninstall_row.pack_start(uninstall_label, False, False, 0)

        uninstall_btn = Gtk.Button(label=i18n.t("settings.uninstall"))
        # Accent, with dark text on it. Left to the desktop theme this
        # came out near-white lettering on a near-white button, which is
        # unreadable — and this is the one button in the app nobody
        # should press by accident. Arming it on the first click still
        # turns it red.
        uninstall_btn.get_style_context().add_class("primary")
        holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        holder.pack_start(uninstall_btn, False, False, 0)
        uninstall_row.pack_start(holder, False, False, 0)
        self._arm_destructive(uninstall_btn, self._on_uninstall)
        box.pack_start(uninstall_row, False, False, 0)

        stats_title = Gtk.Label(label=i18n.t("settings.stats"), xalign=0)
        stats_title.get_style_context().add_class("section-title")
        box.pack_start(stats_title, False, False, 0)

        stats = self.history.stats()
        stats_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                            spacing=24)
        stats_box.pack_start(self._stat(
            str(stats["dictations"]), i18n.t("settings.stats.dictations")),
            False, False, 0)
        stats_box.pack_start(self._stat(
            str(stats["words"]), i18n.t("settings.stats.words")),
            False, False, 0)
        stats_box.pack_start(self._stat(
            MODEL_NAME, i18n.t("settings.stats.model")), False, False, 0)
        box.pack_start(stats_box, False, False, 0)
        return box

    def _stat(self, num, label_text):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        num_lbl = Gtk.Label(label=num, xalign=0)
        num_lbl.get_style_context().add_class("section-title")
        box.pack_start(num_lbl, False, False, 0)
        lbl = Gtk.Label(label=label_text, xalign=0)
        lbl.get_style_context().add_class("hint")
        box.pack_start(lbl, False, False, 0)
        return box

    def _on_view_log(self, _button):
        try:
            with open(LOG_PATH, "r", encoding="utf-8") as f:
                tail = f.readlines()[-300:]
        except OSError:
            tail = []
        dialog = Gtk.Dialog(title=i18n.t("settings.view_log"), parent=self)
        dialog.set_default_size(640, 480)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        view = Gtk.TextView()
        view.set_editable(False)
        view.set_monospace(True)
        view.get_buffer().set_text("".join(tail))
        scroller = Gtk.ScrolledWindow()
        scroller.add(view)
        dialog.get_content_area().pack_start(scroller, True, True, 0)
        dialog.show_all()
        dialog.connect("response", lambda d, *_r: d.destroy())

    def _on_export_all(self, _button):
        dialog = Gtk.FileChooserDialog(
            title=i18n.t("settings.export_all"), parent=self,
            action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons(
            i18n.t("correction.cancel"), Gtk.ResponseType.CANCEL,
            i18n.t("settings.export_all"), Gtk.ResponseType.OK)
        dialog.set_current_name("talkin-export.zip")
        if dialog.run() == Gtk.ResponseType.OK:
            with zipfile.ZipFile(
                    dialog.get_filename(), "w", zipfile.ZIP_DEFLATED) as z:
                for folder in (DATA_DIR, os.path.join(BASE_DIR, "locales")):
                    for name in sorted(os.listdir(folder)):
                        path = os.path.join(folder, name)
                        if not os.path.isfile(path):
                            continue
                        if name == "config.json":
                            # Settings travel; the portal permission
                            # token in them does not. An export is a
                            # file people mail to themselves, and a
                            # capability handed to this machine has no
                            # business riding along in it.
                            z.writestr(os.path.join(
                                "talkin-export", os.path.basename(folder),
                                name), _settings_without_secrets(path))
                            continue
                        z.write(path, os.path.join(
                            "talkin-export",
                            os.path.basename(folder), name))
        dialog.destroy()

    def _check_update(self):
        self._set_update_dot("checking", i18n.t("update.checking"))

        def run():
            from . import updater
            result = updater.check()
            GLib.idle_add(self._update_checked, result)
        import threading
        threading.Thread(target=run, daemon=True).start()
        return False

    def _update_checked(self, result):
        state = result.get("state")
        if state == "available":
            self._update_tag = result["latest"]
            self._set_update_dot("available", i18n.t("update.available_tip"))
            tooltip.flash(self._update_dot)
        elif state == "up-to-date":
            self._set_update_dot("uptodate", i18n.t("update.uptodate"))
            tooltip.flash(self._update_dot)
        else:
            # Put the actual reason in the tooltip. "Can't connect to
            # GitHub" on a machine with a working connection sends
            # someone hunting for a network fault that is not there,
            # when the truth may be a certificate path or a rate limit.
            detail = str(result.get("detail") or "").strip()
            message = i18n.t("update.error")
            if detail:
                message = "{}\n{}".format(message, detail[:160])
            self._set_update_dot("error", message)
            tooltip.flash(self._update_dot, seconds=3.0)
        return False

    def _on_update_dot_clicked(self, _widget, _event):
        # The dot is the whole interface: yellow starts the download,
        # the ready state restarts, and green/red re-check (green to
        # confirm nothing new has shipped since the last check, red to
        # retry after a connection blip - otherwise a stuck red dot
        # would never resolve without closing Settings entirely).
        # checking/downloading ignore clicks; already in progress.
        if self._update_state == "available":
            self._apply_update()
        elif self._update_state == "ready":
            self.app_obj.restart()
        elif self._update_state in ("uptodate", "error"):
            self._check_update()

    def _apply_update(self):
        from . import updater
        self._download_fraction = 0.0
        self._set_update_dot("downloading", i18n.t("update.installing"))

        def on_progress(fraction):
            GLib.idle_add(self._set_download_progress, fraction)

        def run():
            ok = updater.apply(self._update_tag, on_progress=on_progress)
            GLib.idle_add(self._update_applied, ok)
        import threading
        threading.Thread(target=run, daemon=True).start()

    def _set_download_progress(self, fraction):
        self._download_fraction = fraction
        self._update_dot.queue_draw()
        return False

    def _update_applied(self, ok):
        # `result` does not exist here — an earlier edit copied the
        # check's error branch into this one, which would have raised a
        # NameError the first time an update download failed.
        if ok:
            self._set_update_dot("ready", i18n.t("update.restart_tip"))
        else:
            self._set_update_dot("error", i18n.t("update.error"))
        tooltip.flash(self._update_dot, seconds=3.0)
        return False

    # -- close -------------------------------------------------------------

    def _on_close(self, *_args):
        self.hide()
        return True


def open_settings(app_obj, page=None):
    """Show the settings window, creating it once and reusing it after."""
    window = getattr(app_obj, "_settings_window", None)
    if window is None:
        window = SettingsWindow(app_obj)
        app_obj._settings_window = window
    if page:
        window.show_page(page)
    window.show_all()
    window.present()
    # Otherwise GTK auto-focuses the first focusable widget on show,
    # which happens to be the update dot — a persistent focus ring
    # around it with no click involved reads as a rendering bug.
    window.set_focus(None)
