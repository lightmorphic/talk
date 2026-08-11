"""System tray icon and menu via AppIndicator."""

# SPDX-License-Identifier: GPL-3.0-or-later

import os

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import Gtk, AyatanaAppIndicator3 as AppIndicator

from .config import ASSET_DIR
from .i18n import t

_ICONS = {
    "loading": "talkin-thinking",
    "downloading": "talkin-thinking",
    "idle": "talkin-idle",
    "listening": "talkin-listening",
    "thinking": "talkin-thinking",
    "paused": "talkin-paused",
}


class Tray:

    def __init__(self, on_settings, on_toggle_pause, on_restart, on_quit):
        self.on_toggle_pause = on_toggle_pause
        self._indicator = AppIndicator.Indicator.new(
            "talkin", "talkin-idle",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self._indicator.set_icon_theme_path(ASSET_DIR)
        self._indicator.set_title("Lightmorphic Talkin")
        self._indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

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
        self._indicator.set_menu(menu)

    def set_state(self, state):
        icon = _ICONS.get(state, "talkin-idle")
        icon_path = os.path.join(ASSET_DIR, icon + ".svg")
        self._indicator.set_icon_full(
            icon_path, "Lightmorphic Talkin — " + t("tray.status." + state))
        self._status_item.set_label(t("tray.status." + state))
        self._pause_item.set_label(
            t("tray.resume") if state == "paused" else t("tray.pause"))
