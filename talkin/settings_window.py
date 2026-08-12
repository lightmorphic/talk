"""The native Settings window — replaces the old browser-based page.

One Gtk.Window with every section from the original web settings page:
general, hotkeys (native GDK key capture), microphone, output/cleanup,
personal dictionary, history, and maintenance (restart/log/export/update).
Changes are staged in memory and written out with the Save button, same
as the page it replaces.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import os
import time
import zipfile

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango

from . import cleanup, i18n
from .config import ASSET_DIR, BASE_DIR, DATA_DIR, LOG_PATH, DEFAULTS
from .engine import MODEL_NAME, list_microphones
from .hotkeys import MODIFIER_NAMES, combo_is_safe, parse_combo

log = logging.getLogger("talkin.settings")

_YELLOW = "#fbc711"

# One consistent gap between fields/rows/buttons everywhere in this
# window — about 4mm at a standard 96dpi display (~15.1px), rounded to
# the nearest value on the house style's 4px spacing scale.
_FIELD_GAP = 16

# The Lightmorphic style's dark tokens, translated to GTK CSS. This app
# commits to the brand's dark navy + yellow identity always (like the
# tray icons and overlay), rather than following the desktop's light/
# dark setting — there is no "light Talkin" any more than there's a
# grey tray icon.
_CSS = b"""
@define-color lm_bg #050507;
@define-color lm_fg #fafafa;
@define-color lm_panel #24273a;
@define-color lm_panel_border alpha(#ffffff, 0.16);
@define-color lm_border #27272a;
@define-color lm_muted #1c1c1f;
@define-color lm_muted_fg #a1a1aa;
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
}
.talkin-settings button.secondary {
  background-color: @lm_muted;
  border: 1px solid @lm_border;
  color: @lm_fg;
}
.talkin-settings button.primary {
  background-color: @lm_accent;
  background-image: none;
  color: @lm_on_accent;
  font-weight: 600;
  border: none;
}
.talkin-settings button.primary:hover { background-color: @lm_accent_hover; }
.talkin-settings button.danger-armed {
  background-color: @lm_danger_bg;
  color: @lm_danger;
  border: 1px solid @lm_danger;
  font-weight: 600;
}

.talkin-settings .keycap {
  color: @lm_on_accent; background: @lm_accent; font-weight: 600;
  border: none; border-radius: 0.875rem;
}
.talkin-settings .keycap.capturing { background: #ffffff; }

/* A plain ".talkin-settings label" rule would otherwise reach straight
   into these buttons' internal label widget and win over the color
   set above - a direct match always beats inherited color in GTK's
   CSS cascade, regardless of specificity or source order. */
.talkin-settings button.primary label { color: @lm_on_accent; }
.talkin-settings button.secondary label { color: @lm_fg; }
.talkin-settings button.danger-armed label { color: @lm_danger; }
.talkin-settings .keycap label { color: @lm_on_accent; }

@define-color lm_success #4bae4f;
@define-color lm_warning #ffc006;
.talkin-settings .update-dot {
  padding: 0; margin: 0 2px;
  border-radius: 50%;
  border: none;
  background-image: none;
}
.talkin-settings .update-dot.checking { background-color: @lm_muted_fg; }
.talkin-settings .update-dot.uptodate { background-color: @lm_success; }
.talkin-settings .update-dot.available { background-color: @lm_warning; }
.talkin-settings .update-dot.error { background-color: @lm_danger; }
/* A round 12px dot wearing the same square yellow ring as everything
   else looks broken, not focused - and GTK auto-focuses the first
   focusable widget on window show, so this was visible immediately
   on every open with no click at all. A matching round ring instead. */
.talkin-settings .update-dot:focus {
  outline: 2px solid @lm_fg;
  outline-offset: 3px;
}
.talkin-settings entry, .talkin-settings combobox button,
.talkin-settings treeview {
  border-radius: 0.875rem;
}

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

.talkin-settings *:focus {
  outline: 2px solid @lm_accent;
  outline-offset: 2px;
}
/* Buttons that are already accent-colored need a focus ring that
   actually contrasts against them, not more of the same yellow -
   otherwise clicking Save reads as a garish yellow-on-yellow flash. */
.talkin-settings button.primary:focus,
.talkin-settings .keycap:focus {
  outline: 2px solid @lm_bg;
  outline-offset: -4px;
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

# Gdk key name -> the token hotkeys.py's pynput-based listener would
# produce for the same physical key, so a combo captured here fires later.
_GDK_NAME_TO_TOKEN = {
    "Control_L": "ctrl_l", "Control_R": "ctrl_r",
    "Alt_L": "alt_l", "Alt_R": "alt_r", "ISO_Level3_Shift": "alt_r",
    "Shift_L": "shift_l", "Shift_R": "shift_r",
    "Escape": "escape", "Tab": "tab", "ISO_Left_Tab": "tab",
    "Insert": "insert", "Delete": "delete", "Home": "home", "End": "end",
    "Page_Up": "page_up", "Page_Down": "page_down",
    "Up": "up", "Down": "down", "Left": "left", "Right": "right",
    "Pause": "pause", "Scroll_Lock": "scroll_lock", "Menu": "menu",
}
for _i in range(1, 13):
    _GDK_NAME_TO_TOKEN["F{}".format(_i)] = "f{}".format(_i)

_GDK_MODIFIER_KEYVALS = {
    Gdk.KEY_Control_L, Gdk.KEY_Control_R,
    Gdk.KEY_Alt_L, Gdk.KEY_Alt_R, Gdk.KEY_ISO_Level3_Shift,
    Gdk.KEY_Shift_L, Gdk.KEY_Shift_R,
}


def _event_to_token(event):
    """A Gdk key event's own key -> our canonical token string, or None."""
    name = Gdk.keyval_name(event.keyval)
    if not name:
        return None
    if name in _GDK_NAME_TO_TOKEN:
        return _GDK_NAME_TO_TOKEN[name]
    if len(name) == 1:
        return name.lower()
    return None


def _event_to_combo(event):
    """A Gdk key-press event -> a combo string like hotkeys.parse_combo
    understands ("alt+z", "ctrl_r", ...), or None if unusable."""
    trigger = _event_to_token(event)
    if trigger is None:
        return None
    mods = set()
    if event.keyval not in _GDK_MODIFIER_KEYVALS:
        state = event.state
        if state & Gdk.ModifierType.CONTROL_MASK:
            mods.add("ctrl")
        if state & Gdk.ModifierType.MOD1_MASK:
            mods.add("alt")
        if state & Gdk.ModifierType.SHIFT_MASK:
            mods.add("shift")
    ordered = [m for m in MODIFIER_NAMES if m in mods]
    return "+".join(ordered + [trigger])


def _format_combo(text):
    mods, trigger = parse_combo(text)
    if trigger is None:
        return i18n.t("settings.not_set")
    parts = [m.capitalize() for m in ("ctrl", "alt", "shift") if m in mods]
    parts.append(trigger.replace("_", " ").upper() if len(trigger) > 1
                 else trigger.upper())
    return "+".join(parts)


class SettingsWindow(Gtk.Window):
    """Lazily built; call show_settings() to raise it, built once."""

    def __init__(self, app_obj):
        super().__init__(title=i18n.t("settings.title"))
        self.app_obj = app_obj
        self.config = app_obj.config
        self.dictionary = app_obj.dictionary
        self.history = app_obj.history

        self.set_default_size(760, 560)
        self.get_style_context().add_class("talkin-settings")
        _load_bundled_font()
        self._apply_css()
        try:
            self.set_icon_from_file(os.path.join(ASSET_DIR, "talkin.png"))
        except GLib.GError:
            log.warning("could not load settings window icon", exc_info=True)
        self.connect("delete-event", self._on_close)

        self._pending = {}       # config changes not yet saved
        self._capture_field = None
        self._capture_button = None
        self.connect("key-press-event", self._on_window_key)

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
            ("hotkey", "settings.section.hotkey", self._build_hotkeys),
            ("microphone", "settings.section.microphone",
             self._build_microphone),
            ("output", "settings.section.output", self._build_output),
            ("dictionary", "settings.section.dictionary",
             self._build_dictionary),
            ("history", "settings.section.history", self._build_history),
            ("maintenance", "settings.section.maintenance",
             self._build_maintenance),
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

        sidebar.connect("row-selected", self._on_category_selected)
        split.pack_start(sidebar, False, False, 0)
        split.pack_start(self._stack, True, True, 0)

        outer.pack_start(self._build_savebar(), False, False, 0)

        sidebar.select_row(sidebar.get_row_at_index(0))
        self._refresh_dictionary()
        self._refresh_history()

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

    def _secondary_button(self, label_text):
        button = Gtk.Button(label=label_text)
        button.get_style_context().add_class("secondary")
        return button

    def _arm_destructive(self, button, action):
        """A destructive action never fires on one click: the button
        turns red and asks again in place, reverting after a few
        seconds — never a confirm() dialog."""
        original = button.get_label()
        state = {"armed": False, "timeout": None}

        def revert():
            state["armed"] = False
            state["timeout"] = None
            button.set_label(original)
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
            button.set_label(original + "?")
            button.get_style_context().add_class("danger-armed")
            state["timeout"] = GLib.timeout_add_seconds(4, revert)

        button.connect("clicked", on_click)

    def _get(self, key):
        return self._pending.get(key, self.config.get(key))

    def _set(self, key, value):
        self._pending[key] = value

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

        ver_label = Gtk.Label(label="Talkin v{}".format(__version__))
        ver_label.get_style_context().add_class("hint")
        ver_event = Gtk.EventBox()
        ver_event.add(ver_label)
        ver_event.set_tooltip_text("talkin.lightmorphic.co.uk")
        ver_event.connect("button-press-event", self._on_version_clicked)
        ver_event.connect("realize", lambda w: w.get_window().set_cursor(
            Gdk.Cursor.new_from_name(w.get_display(), "pointer")))
        ver_row.pack_start(ver_event, False, False, 0)

        self._update_dot = Gtk.Button()
        self._update_dot.get_style_context().add_class("update-dot")
        # CSS min-width/min-height alone don't force an exact size, so
        # the button was free to stretch taller than wide and render
        # as a slight oval rather than a true circle.
        self._update_dot.set_size_request(13, 13)
        self._update_dot.connect("clicked", self._on_update_dot_clicked)
        ver_row.pack_start(self._update_dot, False, False, 0)
        row.pack_start(ver_row, False, False, 0)

        self._update_state = "checking"
        self._update_tag = None
        self._set_update_dot("checking", i18n.t("update.checking"))
        GLib.idle_add(self._check_update)
        return row

    def _on_version_clicked(self, _widget, _event):
        import webbrowser
        webbrowser.open("https://talkin.lightmorphic.co.uk")

    def _set_update_dot(self, state, tooltip):
        ctx = self._update_dot.get_style_context()
        for cls in ("checking", "uptodate", "available", "error"):
            ctx.remove_class(cls)
        ctx.add_class(state)
        self._update_dot.set_tooltip_text(tooltip)
        self._update_state = state

    # -- general -----------------------------------------------------

    def _build_general(self):
        box = self._section("settings.section.general")

        lang_combo = Gtk.ComboBoxText()
        codes = []
        for code, name in i18n.available_languages():
            lang_combo.append_text(name)
            codes.append(code)
        current = self.config.get("language")
        lang_combo.set_active(codes.index(current) if current in codes else 0)

        def on_lang(combo):
            self._set("language", codes[combo.get_active()])
        lang_combo.connect("changed", on_lang)
        box.pack_start(self._row(i18n.t("settings.language"), lang_combo),
                       False, False, 0)

        autostart = Gtk.CheckButton(label=i18n.t("settings.autostart"))
        autostart.set_active(bool(self.config.get("autostart")))
        autostart.connect(
            "toggled", lambda b: self._set("autostart", b.get_active()))
        box.pack_start(autostart, False, False, 0)

        history_enabled = Gtk.CheckButton(
            label=i18n.t("settings.history_enabled"))
        history_enabled.set_active(bool(self.config.get("history_enabled")))
        history_enabled.connect(
            "toggled",
            lambda b: self._set("history_enabled", b.get_active()))
        box.pack_start(history_enabled, False, False, 0)

        return box

    # -- hotkeys -------------------------------------------------------

    _HOTKEY_FIELDS = [
        ("hotkey_hold", "settings.hotkey_hold", "settings.hotkey_hold_help"),
        ("hotkey_toggle", "settings.hotkey_toggle",
         "settings.hotkey_toggle_help"),
        ("correction_hotkey", "settings.correction_hotkey",
         "settings.correction_hotkey_help"),
    ]

    def _build_hotkeys(self):
        box = self._section("settings.section.hotkey",
                            "settings.hotkey_intro")
        self._hotkey_buttons = {}
        for field, title_key, help_key in self._HOTKEY_FIELDS:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=_FIELD_GAP)
            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            labels.set_size_request(220, -1)
            title = Gtk.Label(label=i18n.t(title_key), xalign=0)
            labels.pack_start(title, False, False, 0)
            hint = Gtk.Label(label=i18n.t(help_key), xalign=0, wrap=True)
            hint.get_style_context().add_class("hint")
            labels.pack_start(hint, False, False, 0)
            row.pack_start(labels, True, True, 0)

            button = Gtk.Button(label=_format_combo(self._get(field)))
            button.get_style_context().add_class("keycap")
            button.connect("clicked", self._start_capture, field)
            self._hotkey_buttons[field] = button
            row.pack_start(button, False, False, 0)

            clear = Gtk.Button.new_from_icon_name(
                "edit-clear-symbolic", Gtk.IconSize.BUTTON)
            clear.get_style_context().add_class("secondary")
            clear.set_tooltip_text(i18n.t("settings.clear_key"))
            clear.connect("clicked", lambda *_r, f=field: self._clear_key(f))
            row.pack_start(clear, False, False, 0)

            box.pack_start(row, False, False, 0)

        self._hotkey_status = Gtk.Label(xalign=0, wrap=True)
        self._hotkey_status.get_style_context().add_class("hint")
        box.pack_start(self._hotkey_status, False, False, 0)
        return box

    def _start_capture(self, button, field):
        if self._capture_button is not None:
            self._end_capture()
        self._capture_field = field
        self._capture_button = button
        button.set_label(i18n.t("settings.press_keys"))
        button.get_style_context().add_class("capturing")
        self._hotkey_status.set_text("")
        self.grab_focus()

    def _end_capture(self):
        if self._capture_button is not None:
            self._capture_button.get_style_context().remove_class("capturing")
            self._capture_button.set_label(
                _format_combo(self._get(self._capture_field)))
        self._capture_field = None
        self._capture_button = None

    def _clear_key(self, field):
        self._set(field, "")
        self._hotkey_buttons[field].set_label(i18n.t("settings.not_set"))

    def _on_window_key(self, _widget, event):
        if self._capture_field is None:
            return False
        if event.keyval in (Gdk.KEY_Escape,) and \
                event.keyval not in _GDK_MODIFIER_KEYVALS:
            field = self._capture_field
            self._end_capture()
            return True
        combo = _event_to_combo(event)
        if combo is None:
            return True
        field = self._capture_field
        if not combo_is_safe(combo):
            self._hotkey_status.set_text(i18n.t("settings.hotkey_unsafe"))
            return True
        others = [f for f, *_r in self._HOTKEY_FIELDS if f != field]
        if combo in (self._get(f) for f in others):
            self._hotkey_status.set_text(i18n.t("settings.hotkey_duplicate"))
            return True
        self._set(field, combo)
        self._end_capture()
        return True

    # -- microphone ----------------------------------------------------

    def _build_microphone(self):
        box = self._section("settings.section.microphone")
        self._mic_combo = Gtk.ComboBoxText()
        self._mic_ids = []
        current = self.config.get("mic")
        active = 0
        for i, (mic_id, name) in enumerate(list_microphones()):
            label = (i18n.t("settings.mic.default") if mic_id == "default"
                     else name)
            self._mic_combo.append_text(label)
            self._mic_ids.append(mic_id)
            if mic_id == current:
                active = i
        self._mic_combo.set_active(active)
        self._mic_combo.connect(
            "changed",
            lambda c: self._set("mic", self._mic_ids[c.get_active()]))
        box.pack_start(self._row(i18n.t("settings.mic"), self._mic_combo),
                       False, False, 0)

        test_btn = self._secondary_button(i18n.t("settings.mic_test"))
        test_btn.connect("clicked", self._on_mic_test)
        box.pack_start(test_btn, False, False, 0)

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
        injection = Gtk.ComboBoxText()
        injection.append("paste", i18n.t("settings.injection.paste"))
        injection.append("type", i18n.t("settings.injection.type"))
        injection.set_active_id(self.config.get("injection"))
        injection.connect(
            "changed", lambda c: self._set("injection", c.get_active_id()))
        box.pack_start(self._row(i18n.t("settings.injection"), injection),
                       False, False, 0)

        cleanup_title = Gtk.Label(label=i18n.t("settings.section.cleanup"),
                                  xalign=0)
        cleanup_title.get_style_context().add_class("section-title")
        box.pack_start(cleanup_title, False, False, 0)

        fillers = Gtk.CheckButton(label=i18n.t("settings.cleanup_fillers"))
        fillers.set_active(bool(self.config.get("cleanup_fillers")))
        fillers.connect(
            "toggled", lambda b: self._set("cleanup_fillers", b.get_active()))
        box.pack_start(fillers, False, False, 0)

        dict_toggle = Gtk.CheckButton(
            label=i18n.t("settings.cleanup_dictionary"))
        dict_toggle.set_active(bool(self.config.get("cleanup_dictionary")))
        dict_toggle.connect(
            "toggled",
            lambda b: self._set("cleanup_dictionary", b.get_active()))
        box.pack_start(dict_toggle, False, False, 0)
        return box

    # -- dictionary ------------------------------------------------------

    def _build_dictionary(self):
        box = self._section("settings.section.dictionary",
                            "settings.dictionary_help")

        self._dict_store = Gtk.ListStore(str, str)
        tree = Gtk.TreeView(model=self._dict_store)
        tree.append_column(Gtk.TreeViewColumn(
            i18n.t("settings.dict.heard"), Gtk.CellRendererText(), text=0))
        tree.append_column(Gtk.TreeViewColumn(
            i18n.t("settings.dict.say"), Gtk.CellRendererText(), text=1))
        remove_renderer = Gtk.CellRendererText(
            text=i18n.t("settings.dict.remove"), foreground=_YELLOW)
        remove_col = Gtk.TreeViewColumn("", remove_renderer)
        tree.append_column(remove_col)
        tree.connect("row-activated", self._on_dict_row_activated)

        scroller = Gtk.ScrolledWindow()
        scroller.set_min_content_height(140)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(tree)
        box.pack_start(scroller, False, False, 0)

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
        add_btn = self._secondary_button(i18n.t("settings.dict.add"))
        add_btn.connect("clicked", self._on_dict_add)
        actions.pack_start(add_btn, False, False, 0)
        export_btn = self._secondary_button(i18n.t("settings.dict.export"))
        export_btn.connect("clicked", self._on_dict_export)
        actions.pack_start(export_btn, False, False, 0)
        import_btn = self._secondary_button(i18n.t("settings.dict.import"))
        import_btn.connect("clicked", self._on_dict_import)
        actions.pack_start(import_btn, False, False, 0)
        box.pack_start(actions, False, False, 0)
        return box

    def _refresh_dictionary(self):
        self._dict_store.clear()
        entries = self.dictionary.entries()
        for e in entries:
            self._dict_store.append([e["heard"], e["say"]])
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
                for e in data["entries"]:
                    heard = str(e.get("heard", "")).strip()
                    say = str(e.get("say", "")).strip()
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

        self._history_store = Gtk.ListStore(str, str)
        tree = Gtk.TreeView(model=self._history_store)
        when_col = Gtk.TreeViewColumn(
            "", Gtk.CellRendererText(), text=0)
        tree.append_column(when_col)
        text_renderer = Gtk.CellRendererText(
            wrap_width=360, wrap_mode=Pango.WrapMode.WORD_CHAR)
        tree.append_column(Gtk.TreeViewColumn("", text_renderer, text=1))

        scroller = Gtk.ScrolledWindow()
        scroller.set_min_content_height(160)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(tree)
        box.pack_start(scroller, False, False, 0)

        self._history_empty = Gtk.Label(
            label=i18n.t("settings.history.empty"), xalign=0)
        self._history_empty.get_style_context().add_class("hint")
        self._history_empty.set_no_show_all(True)
        box.pack_start(self._history_empty, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=_FIELD_GAP)
        export_btn = self._secondary_button(i18n.t("settings.history.export"))
        export_btn.connect("clicked", self._on_history_export)
        actions.pack_start(export_btn, False, False, 0)
        clear_btn = self._secondary_button(i18n.t("settings.history.clear"))
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

    def _build_maintenance(self):
        box = self._section("settings.section.maintenance",
                            "settings.maintenance_help")

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=_FIELD_GAP)
        restart_btn = self._secondary_button(i18n.t("settings.restart"))
        restart_btn.connect(
            "clicked", lambda *_r: self.app_obj.restart())
        actions.pack_start(restart_btn, False, False, 0)

        log_btn = self._secondary_button(i18n.t("settings.view_log"))
        log_btn.connect("clicked", self._on_view_log)
        actions.pack_start(log_btn, False, False, 0)

        export_btn = self._secondary_button(i18n.t("settings.export_all"))
        export_btn.connect("clicked", self._on_export_all)
        actions.pack_start(export_btn, False, False, 0)
        box.pack_start(actions, False, False, 0)

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
                        if os.path.isfile(path):
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
            self._set_update_dot("available", "{} {}".format(
                i18n.t("update.available"), result["latest"]))
            self._show_update_available_dialog()
        elif state == "up-to-date":
            self._set_update_dot("uptodate", i18n.t("update.uptodate"))
        else:
            detail = result.get("detail")
            tooltip = i18n.t("update.error")
            if detail:
                tooltip = "{}: {}".format(tooltip, detail)
            self._set_update_dot("error", tooltip)
        return False

    def _on_update_dot_clicked(self, _button):
        # Any click re-checks; a click while an update is already known
        # to be available re-opens the install prompt rather than
        # applying silently — a whole new AppImage swapping itself in
        # from one click with no confirmation is a bit much.
        if self._update_state == "available":
            self._show_update_available_dialog()
        else:
            self._check_update()

    def _show_update_available_dialog(self):
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.INFO,
            text=i18n.t("update.popup_title"),
            secondary_text=i18n.t("update.popup_body").format(
                v=self._update_tag))
        dialog.add_button(i18n.t("update.not_now"), Gtk.ResponseType.CANCEL)
        dialog.add_button(
            i18n.t("update.button").format(v=self._update_tag),
            Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            self._apply_update()

    def _apply_update(self):
        from . import updater
        self._set_update_dot("checking", i18n.t("update.installing"))

        def run():
            ok = updater.apply(self._update_tag)
            GLib.idle_add(self._update_applied, ok)
        import threading
        threading.Thread(target=run, daemon=True).start()

    def _update_applied(self, ok):
        if ok:
            self._set_update_dot("uptodate", i18n.t("update.ready_body"))
            self._show_restart_dialog()
        else:
            self._set_update_dot("error", i18n.t("update.failed"))
        return False

    def _show_restart_dialog(self):
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.INFO,
            text=i18n.t("update.ready_title"),
            secondary_text=i18n.t("update.ready_body"))
        dialog.add_button(i18n.t("update.not_now"), Gtk.ResponseType.CANCEL)
        dialog.add_button(
            i18n.t("update.restart_now"), Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            self.app_obj.restart()

    # -- save / close ----------------------------------------------------

    def _build_savebar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bar.set_margin_top(8)
        bar.set_margin_bottom(12)
        bar.set_margin_start(24)
        bar.set_margin_end(24)
        self._save_status = Gtk.Label(xalign=0)
        self._save_status.get_style_context().add_class("hint")
        bar.pack_start(self._save_status, True, True, 0)
        save_btn = Gtk.Button(label=i18n.t("settings.save"))
        save_btn.get_style_context().add_class("primary")
        save_btn.connect("clicked", self._on_save)
        bar.pack_start(save_btn, False, False, 0)
        return bar

    def _on_save(self, _button):
        if not self._pending:
            return
        combos = [self._get(f) for f, *_r in self._HOTKEY_FIELDS
                 if self._get(f)]
        if len(combos) != len(set(combos)):
            self._save_status.set_text(i18n.t("settings.hotkey_duplicate"))
            return
        changes = dict(self._pending)
        self._pending.clear()
        self.config.update(changes)
        if "autostart" in changes:
            from .config import set_autostart
            set_autostart(changes["autostart"])
        self.app_obj.apply_settings()
        self._save_status.set_text(i18n.t("settings.saved"))

    def _on_close(self, *_args):
        self.hide()
        return True


def open_settings(app_obj):
    """Show the settings window, creating it once and reusing it after."""
    window = getattr(app_obj, "_settings_window", None)
    if window is None:
        window = SettingsWindow(app_obj)
        app_obj._settings_window = window
    window.show_all()
    window.present()
    # Otherwise GTK auto-focuses the first focusable widget on show,
    # which happens to be the update dot — a persistent focus ring
    # around it with no click involved reads as a rendering bug.
    window.set_focus(None)
