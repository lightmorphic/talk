"""The Talkin application: wires hotkeys, audio, model, UI together."""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import os
import shutil
import subprocess
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from . import cleanup, config as cfg, correction, i18n, injector
from .engine import Recorder, Transcriber
from .hotkeys import Hotkeys
from .overlay import Overlay
from .tray import Tray

log = logging.getLogger("talkin.app")


class TalkinApp:

    def __init__(self):
        self.config = cfg.Config()
        self.dictionary = cfg.Dictionary()
        self.history = cfg.History(self.config)
        i18n.set_language(self.config.get("language"))

        self.state = "loading"
        self.overlay = Overlay()
        self.tray = Tray(
            on_settings=self.open_settings,
            on_toggle_pause=self.toggle_pause,
            on_restart=self.restart,
            on_quit=self.quit)

        self.recorder = Recorder(self.config, on_level=self.overlay.push_level)
        self.transcriber = Transcriber(
            on_ready=lambda: GLib.idle_add(self._model_ready),
            on_error=lambda key: GLib.idle_add(self._fail, key),
            on_downloading=lambda: GLib.idle_add(self._downloading))
        self.hotkeys = Hotkeys(
            self.config,
            on_hold_press=self._hold_press,
            on_hold_release=self._hold_release,
            on_toggle=self._toggle,
            on_correction=self._correction)
        self._recording_via = None  # "hold" | "toggle" | None

        from .web.server import start_server
        self.server_url = start_server(self)

        cfg.set_autostart(self.config.get("autostart"))

    # -- state -------------------------------------------------------

    def _set_state(self, state):
        self.state = state
        self.tray.set_state(state)
        if state == "listening":
            self.overlay.show_listening()
        elif state == "thinking":
            self.overlay.show_thinking()
        else:
            self.overlay.hide_overlay()

    def _model_ready(self):
        if self.state in ("loading", "downloading"):
            self._set_state("idle")
            self.notify(i18n.t("notify.ready"))

    def _downloading(self):
        self._set_state("downloading")
        self.notify(i18n.t("notify.downloading"))

    def _fail(self, error_key):
        self._set_state("idle" if self.transcriber.ready else "paused")
        self.notify(i18n.t(error_key))

    # -- dictation flow ----------------------------------------------

    def _can_start(self):
        return self.state == "idle" and self.transcriber.ready

    def _hold_press(self):
        if self._can_start():
            self._start_recording("hold")

    def _hold_release(self):
        if self.state == "listening" and self._recording_via == "hold":
            self._finish_recording()

    def _toggle(self):
        if self.state == "listening" and self._recording_via == "toggle":
            self._finish_recording()
        elif self._can_start():
            self._start_recording("toggle")

    def _start_recording(self, via):
        try:
            self.recorder.start()
        except Exception:
            log.exception("could not open microphone")
            self.notify(i18n.t("error.mic"))
            return
        self._recording_via = via
        log.info("listening (mic open, via %s)", via)
        self._set_state("listening")

    def _finish_recording(self):
        audio = self.recorder.stop()
        self._recording_via = None
        log.info("recorded %.1fs, transcribing", len(audio) / 16000)
        self._set_state("thinking")
        self.transcriber.submit(
            audio,
            lambda text, err: GLib.idle_add(self._transcribed, text, err))

    def _transcribed(self, text, error_key):
        if error_key is not None:
            self._fail(error_key)
            return
        raw = text or ""
        clean = cleanup.clean(raw, self.config, self.dictionary)
        log.info("transcribed %d chars", len(clean))
        if not clean:
            self._set_state("idle")
            return
        self.history.add(raw, clean)
        injector.inject(clean, self.config, self._injected)

    def _injected(self, ok):
        self._set_state("idle")
        if not ok:
            self.notify(i18n.t("error.inject"))

    # -- correction --------------------------------------------------

    def _correction(self):
        if self.state in ("listening", "thinking"):
            return
        correction.open_correction(self.dictionary, self.notify)

    # -- controls ----------------------------------------------------

    def toggle_pause(self):
        if self.state == "paused":
            self._set_state("idle" if self.transcriber.ready else "loading")
        else:
            if self.recorder.recording:
                self.recorder.stop()
                self._recording_via = None
            self._set_state("paused")

    def open_settings(self):
        subprocess.Popen(["xdg-open", self.server_url],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)

    def apply_settings(self):
        """Called by the web server after config changes."""
        i18n.set_language(self.config.get("language"))
        self.hotkeys.reload()
        return True

    def restart(self):
        log.info("restarting")
        subprocess.Popen([cfg.launcher_path()], cwd=cfg.BASE_DIR)
        self.quit()

    def quit(self):
        try:
            self.hotkeys.stop()
        except Exception:
            pass
        Gtk.main_quit()

    def notify(self, message):
        log.info("notify: %s", message)
        if shutil.which("notify-send"):
            subprocess.Popen(
                ["notify-send", "--app-name", "Lightmorphic Talkin",
                 "--icon", os.path.join(cfg.ASSET_DIR, "talkin-idle.svg"),
                 i18n.t("notify.title"), message],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    cfg.setup_logging()
    log.info("Talkin starting (pid %s)", os.getpid())

    # Without this, GLib falls back to argv[0]'s basename for the
    # process identity — which is literally "__main__.py" when running
    # via `python -m talkin`, and that's what desktop environments show
    # as the tray icon's hover tooltip. Must run before any GTK/GLib
    # object (Tray, Overlay, dialogs) is created.
    GLib.set_prgname("talkin")
    GLib.set_application_name("Lightmorphic Talkin")

    # One instance only: a lock on a well-known abstract socket. During
    # a self-update restart the old instance may hold the lock for a
    # moment longer, so retry briefly before concluding we're a duplicate.
    import socket
    import time
    global _single
    _single = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    for attempt in range(20):
        try:
            _single.bind("\0talkin-single-instance")
            break
        except OSError:
            time.sleep(0.5)
    else:
        print("Talkin is already running.", file=sys.stderr)
        sys.exit(0)

    app = TalkinApp()
    GLib.idle_add(lambda: app.tray.set_state("loading") and False)
    Gtk.main()
