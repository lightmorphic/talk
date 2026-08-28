"""The Talkin application: wires audio, model and UI together."""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import os
import shutil
import subprocess
import sys
import tempfile

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from . import cleanup, config as cfg, correction, i18n, injector, session, uninstall
from .engine import Recorder, Transcriber
from .download_window import DownloadWindow
from .float_button import FloatButton
from . import sounds
from .settings_window import open_settings
from .tray import Tray

log = logging.getLogger("talkin.app")


class TalkinApp:

    def __init__(self):
        self.config = cfg.Config()
        self.dictionary = cfg.Dictionary()
        self.history = cfg.History(self.config)
        i18n.set_language(self.config.get("language"))

        self.state = "loading"
        # Left-clicking the tray icon starts a dictation and clicking
        # again stops it, so the button and the tray icon behave
        # share _toggle(). The old mid-screen overlay circle is gone;
        # the tray icon itself animates while listening/transcribing,
        # fed the live mic level below.
        self._download_window = None
        self._progress_fraction = 0.0
        self._announced_ready = False
        self.tray = Tray(
            on_settings=self.open_settings,
            on_toggle_pause=self.toggle_pause,
            on_restart=self.restart,
            on_quit=self.quit,
            on_activate=self._tray_click,
            on_show_button=self._show_float_button)

        self.float_button = None
        if self.config.get("float_button"):
            self.float_button = FloatButton(on_toggle=self._toggle,
                                            on_correction=self._correction)

        self.recorder = Recorder(self.config, on_level=self._on_level)
        self.transcriber = Transcriber(
            on_ready=lambda: GLib.idle_add(self._model_ready),
            on_error=lambda key: GLib.idle_add(self._fail, key),
            on_downloading=lambda: GLib.idle_add(self._downloading))
        self._listening = False

        # On Wayland this opens the portal session now, so the consent
        # prompt lands at startup rather than mid-sentence on the first
        # dictation. On X11 there is nothing to consent to.
        try:
            injector.setup(self.config, on_lost=self._injection_lost,
                           on_ready=self._injection_ready)
        except injector.InjectionUnavailable as exc:
            log.error("no way to type into other windows: %s", exc)
            key = ("error.no_input_portal" if session.is_wayland()
                   else "error.no_input_x11")
            GLib.idle_add(lambda: (self.notify(i18n.t(key)), False)[1])

        # Show the first-run notice even if the model is already there:
        # the permission step applies either way, and skipping it is the
        # failure that looks like the app being broken.
        if not self.config.get("first_run_seen"):
            GLib.idle_add(self._show_first_run)

        cfg.set_autostart(self.config.get("autostart"))
        # Deleting the AppImage runs none of our code, so leave a watcher
        # that clears the leftovers at the next login if we are gone.
        uninstall.install_cleanup_hook()

    # -- state -------------------------------------------------------

    def _on_level(self, level):
        self.tray.set_level(level)
        if self.float_button is not None:
            self.float_button.set_level(level)

    def _set_state(self, state):
        self.state = state
        self.tray.set_state(state)
        if self.float_button is not None:
            self.float_button.set_state(state)

    def _model_ready(self):
        was_downloading = self.state == "downloading"
        if self._download_window is not None:
            self._download_window.finish()
            self._download_window = None
        if self.state in ("loading", "downloading"):
            self._set_state("idle")
            # "Ready" must mean ready. Without permission to type, the app
            # can hear perfectly and produce nothing, so saying it is ready
            # at that point is simply untrue.
            log.info("model ready; can type = %s", injector.ready())
            if not injector.ready():
                self.notify(i18n.t("notify.needs_permission"))
            else:
                self.notify(i18n.t("notify.download_done") if was_downloading
                            else i18n.t("notify.ready"))

    def _downloading(self):
        """First run: the model is being fetched.

        A spinning tray icon is not enough. A 600 MB download can take
        minutes and can stall for minutes more, and with nothing on
        screen that is indistinguishable from a broken app — which is
        exactly how it was reported. Show the size, the progress, and
        whether it is still moving.
        """
        self._set_state("downloading")
        self.notify(i18n.t("notify.downloading"))
        if self._download_window is None:
            self._download_window = DownloadWindow(
                self.config,
                on_progress=self._on_progress,
                on_dismissed=self._download_hidden,
                on_quit=self.quit,
                is_ready=injector.ready,
                on_language_changed=self._rebuild_first_run)

    def _rebuild_first_run(self):
        """Redraw the first-run window in the language just chosen.

        Same reason Settings is rebuilt rather than patched: every label
        was set when its widget was built. This window carries its
        progress and any failure across, so switching language does not
        interrupt a download.
        """
        window = self._download_window
        if window is None:
            return
        state = window.snapshot()
        window.destroy()
        self._download_window = None
        self._downloading() if self.state == "downloading" \
            else self._show_first_run()
        if self._download_window is not None:
            self._download_window.restore(state)
        try:
            self.tray.retranslate()
        except Exception:
            log.exception("could not retranslate the tray")

    def _download_hidden(self):
        """They closed the notice: point at where the progress now lives."""
        if self.state == "downloading":
            self.notify(i18n.t("notify.download_in_tray").format(
                percent=int(self._progress_fraction * 100)))

    def _show_first_run(self):
        if self._download_window is None:
            self._download_window = DownloadWindow(
                self.config,
                on_progress=self._on_progress,
                on_dismissed=self._download_hidden,
                on_quit=self.quit,
                is_ready=injector.ready,
                on_language_changed=self._rebuild_first_run)
        self.config.update({"first_run_seen": True})
        return False

    def _on_progress(self, fraction):
        self._progress_fraction = fraction
        self.tray.set_progress(fraction)
        if self.float_button is not None:
            self.float_button.set_progress(fraction)

    def _fail(self, error_key):
        if self._download_window is not None and not self.transcriber.ready:
            # Keep the window: it is the only place that can explain what
            # went wrong and offer another go. Closing it here was what
            # left a pause mark on the icon and no explanation anywhere.
            self._download_window.fail(i18n.t("download.failed"),
                                       on_retry=self._retry_download)
        elif self._download_window is not None:
            self._download_window.finish()
            self._download_window = None
        self._set_state("idle" if self.transcriber.ready else "paused")
        self.notify(i18n.t(error_key))

    def _retry_download(self):
        """Start the download again after it gave up."""
        if self.transcriber.ready:
            return
        log.info("retrying the model download")
        if self.transcriber.retry():
            self._set_state("downloading")

    # -- dictation flow ----------------------------------------------

    def _can_start(self):
        return self.state == "idle" and self.transcriber.ready

    def _blip(self, which):
        if self.config.get("sounds"):
            sounds.play(which)

    def _tray_click(self):
        """Left-click on the tray icon: get the floating button back.

        The floating button is how dictation is started, so a click on the
        tray means "where did my button go" far more often than it means
        "start recording". It is also the only recovery there is: on
        Wayland an application cannot force itself above a browser, so the
        button does get buried, and re-showing it is the way back.

        With the floating button switched off in settings there is nothing
        to show, and the click starts and stops dictation as it used to.
        """
        if self.float_button is None:
            self._toggle()
            return
        self._show_float_button()

    def _show_float_button(self):
        """Bring the floating button back to the front.

        Raising an existing window is not enough on GNOME: "keep above" is
        a hint the compositor is free to ignore, and it does, so a window
        buried under a browser stays buried however often it is raised.
        Un-mapping and re-mapping it is different — the compositor treats
        it as a newly opened window and stacks it on top, which is the
        only lever that actually works.

        The cost is the position: a re-mapped window opens wherever the
        compositor puts it, and on Wayland an application is not allowed
        to say where. So this can move the button, and it has to be
        dragged back. Being somewhere visible beats being invisible in
        the right place.
        """
        button = self.float_button
        if button is None:
            return
        try:
            button.hide()

            def remap():
                button.show_all()
                button.present()
                button.place_default()
                button.set_keep_above(True)
                window = button.get_window()
                if window is not None:
                    window.raise_()
                return False

            # A beat between the two, or the compositor coalesces them
            # into no change at all and the button never moves in the
            # stack.
            GLib.timeout_add(120, remap)
            log.info("floating button re-shown from the tray")
        except Exception:
            log.exception("could not re-show the floating button")

    def _toggle(self):
        if self.state == "listening" and self._listening:
            self._finish_recording()
        elif self._can_start():
            self._start_recording()

    def _start_recording(self):
        try:
            self.recorder.start()
        except Exception:
            log.exception("could not open microphone")
            self.notify(i18n.t("error.mic"))
            return
        self._listening = True
        self._blip("start")
        log.info("listening: microphone open")
        self._set_state("listening")

    def _finish_recording(self):
        audio = self.recorder.stop()
        self._listening = False
        self._blip("stop")
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
        # Log both lengths: "transcribed 0 chars" on its own cannot tell
        # apart "heard nothing" from "heard something and cleanup ate it",
        # and those need completely different fixes.
        # Counts only, never the words. The log exists to diagnose faults
        # and is read by other people; what was dictated is nobody's
        # business but the speaker's, and privacy that depends on
        # remembering not to log something is not privacy.
        log.info("transcribed %d chars (raw %d)", len(clean), len(raw))
        if not clean and raw.strip():
            # Cleanup emptied a real transcript — usually an utterance made
            # entirely of hesitation sounds. Type what was actually said
            # rather than silently dropping it; losing words is far worse
            # than leaving an "um" in.
            log.info("cleanup emptied a non-empty transcript; using it as-is")
            clean = raw.strip()
        if not clean:
            log.info("nothing recognised in the audio")
            self._set_state("idle")
            return
        self.history.add(raw, clean)
        injector.inject(clean, self.config, self._injected)

    def _injected(self, ok):
        self._set_state("idle")
        if not ok:
            self.notify(i18n.t("error.inject"))

    # -- correction --------------------------------------------------

    def _injection_ready(self, ok):
        """Permission came through — say so, once the model is loaded too."""
        if ok and self.transcriber.ready and self.state != "listening":
            self.notify(i18n.t("notify.ready"))
        return False

    def _injection_lost(self):
        """The desktop withdrew permission to type into other windows.

        Usually the user pressing 'stop' on the desktop's own input-control
        indicator. Say so, because otherwise Talkin looks alive but never
        types again — which reads as a crash.
        """
        self.notify(i18n.t("error.input_permission_lost"))

    def _correction(self):
        if self.state in ("listening", "thinking"):
            return
        correction.open_correction(self.dictionary, self.notify)

    # -- controls ----------------------------------------------------

    def toggle_pause(self):
        if self.state == "paused":
            if not self.transcriber.ready:
                # Paused after a failed download used to mean a spinning
                # icon with nothing running behind it. Resume has to
                # actually start the fetch again.
                self._retry_download()
                if self.state != "downloading":
                    self._set_state("loading")
                return
            self._set_state("idle")
        else:
            if self.recorder.recording:
                self.recorder.stop()
                self._listening = False
            self._set_state("paused")

    def open_settings(self):
        open_settings(self)

    def apply_settings(self):
        """Re-read anything that can change while Talkin is running."""
        i18n.set_language(self.config.get("language"))
        return True

    def retranslate(self):
        """Put the whole interface into the language just chosen.

        Labels are set once, when each widget is built, so changing the
        language only changed what the NEXT run would say. The tray menu
        is rebuilt in place; Settings is rebuilt wholesale, because
        every string in it was baked in at construction.
        """
        i18n.set_language(self.config.get("language"))
        try:
            self.tray.retranslate()
        except Exception:
            log.exception("could not retranslate the tray")
        window = getattr(self, "_settings_window", None)
        if window is not None:
            page = window.current_page()
            window.destroy()
            self._settings_window = None
            open_settings(self, page=page)

    # Variables the AppImage runtime and its GTK hook inject into this
    # process. They point into THIS build's temporary mount, so handing
    # them to a replacement makes it load this bundle's libraries and
    # Python — including when its own runtime shells out to mount itself.
    # That is how a restart ends as exit code 127: the new copy runs and
    # cannot find what it needs.
    _APPIMAGE_VARS = (
        "APPDIR", "APPIMAGE", "ARGV0", "OWD",
        "LD_LIBRARY_PATH", "PYTHONHOME", "PYTHONPATH",
        "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE",
        "GI_TYPELIB_PATH", "GSETTINGS_SCHEMA_DIR",
        "GDK_PIXBUF_MODULE_FILE", "GDK_BACKEND",
        "GTK_DATA_PREFIX", "GTK_EXE_PREFIX", "GTK_IM_MODULE_FILE",
        "GTK_PATH", "GTK_THEME",
    )

    @staticmethod
    def _clean_environment():
        """This process's environment with the AppImage's own bits taken out.

        The replacement sets all of these again for itself, from its own
        mount, so removing them costs nothing and stops it inheriting
        paths into a mount that is about to disappear.
        """
        env = dict(os.environ)
        appdir = env.get("APPDIR")
        for name in TalkinApp._APPIMAGE_VARS:
            env.pop(name, None)
        # PATH and XDG_DATA_DIRS are prepended to rather than replaced,
        # so they keep the user's real values — only our own entries go.
        if appdir:
            for name in ("PATH", "XDG_DATA_DIRS"):
                value = env.get(name)
                if not value:
                    continue
                kept = [part for part in value.split(os.pathsep)
                        if part and not part.startswith(appdir)]
                if kept:
                    env[name] = os.pathsep.join(kept)
                else:
                    env.pop(name, None)
        return env

    def restart(self):
        log.info("restarting")
        # The replacement starts in the home folder. Left to inherit
        # this process's working directory it would start inside this
        # build's temporary mount — a directory that disappears seconds
        # later. A process whose working directory has been deleted still
        # runs, but every library call that asks where it is fails with
        # "No such file or directory", and that is how a fresh install
        # ended up unable to download its own speech model.
        #
        # start_new_session puts the replacement in a session of its own.
        # Without it the new process is a child in this one's process
        # group, sharing this terminal and this session, and it went down
        # with the parent instead of coming back — the app appeared to
        # quit rather than restart.
        launcher = cfg.launcher_path()
        # Say what we are about to run, and whether it is runnable. When
        # a restart quietly fails, this is the line that explains it —
        # the update replaces the AppImage file underneath us, and a
        # replacement that is missing or not executable looks from the
        # outside exactly like a button that does nothing.
        log.info("restart target: %s (exists=%s, executable=%s)", launcher,
                 os.path.exists(launcher), os.access(launcher, os.X_OK))
        if not os.access(launcher, os.X_OK):
            log.error("the restart target cannot be executed")
            self._restart_failed()
            return
        # Keep the replacement's complaints, rather than throwing them
        # away. When it dies at once, its own last words are the only
        # thing that explains why.
        errors = tempfile.TemporaryFile()
        try:
            child = subprocess.Popen(
                [launcher], start_new_session=True, close_fds=True,
                env=self._clean_environment(), cwd=os.path.expanduser("~"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=errors)
            log.info("replacement started as pid %s", child.pid)
        except OSError:
            log.exception("could not start the replacement process")
            self._restart_failed()
            return

        # Checked more than once. A replacement that dies at two seconds
        # is as dead as one that dies at one, and a single early look
        # missed it — leaving this process to quit anyway and the whole
        # thing to read as the app simply closing.
        looks = [0]

        def when_up():
            """Only leave once the replacement is actually on its feet.

            Quitting first and hoping is what makes a failed restart look
            like the app simply dying: the icon goes, nothing comes back,
            and the person is left watching an empty screen. If the new
            process is already gone, this one stays and says so.
            """
            if child.poll() is not None:
                log.error("the replacement exited immediately (code %s)",
                          child.returncode)
                try:
                    errors.seek(0)
                    said = errors.read().decode("utf-8", "replace").strip()
                    if said:
                        log.error("it said: %s", said[-600:])
                except Exception:
                    pass
                self._restart_failed()
                return False
            looks[0] += 1
            if looks[0] < 3:
                return True
            # Hand over the single-instance lock only now. Releasing it
            # before knowing the replacement is alive would leave the
            # door open with nobody coming through; the new process
            # retries the bind for ten seconds, so waiting costs it
            # nothing.
            global _single
            try:
                _single.close()
            except Exception:
                pass
            self.quit()
            return False

        GLib.timeout_add(800, when_up)

    def _restart_failed(self):
        """Say it plainly rather than leaving someone waiting."""
        self._set_state("idle" if self.transcriber.ready else "paused")
        self.notify(i18n.t("error.restart_failed"))

    def quit(self):
        # Take everything off the screen first. The tear-down below is
        # quick, but the interpreter's own exit is not — unloading the
        # speech model and its native thread pool took half a minute in
        # use — and until the icon disappears the app looks hung. So the
        # visible parts go now and the rest follows.
        try:
            self.tray.hide()
        except Exception:
            pass
        try:
            if self.float_button is not None:
                self.float_button.stop()
        except Exception:
            pass
        try:
            injector.shutdown()
        except Exception:
            pass
        try:
            sounds.cleanup()
        except Exception:
            pass
        Gtk.main_quit()

    def notify(self, message):
        log.info("notify: %s", message)
        if shutil.which("notify-send"):
            subprocess.Popen(
                ["notify-send", "--app-name", "Talkin",
                 "--icon", os.path.join(cfg.ASSET_DIR, "talkin-idle.svg"),
                 i18n.t("notify.title"), message],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _log_uncaught(exc_type, exc_value, exc_tb):
    """Put crashes in the log file, not only on a terminal nobody sees.

    Launched from a desktop icon there is no terminal, so an unhandled
    exception vanished with the process and left only "it crashed" to go
    on. This makes the next one diagnosable.
    """
    log.critical("unhandled exception",
                 exc_info=(exc_type, exc_value, exc_tb))
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def main():
    cfg.setup_logging()
    sys.excepthook = _log_uncaught
    # GTK swallows exceptions raised inside signal handlers and prints
    # them to stderr, which is likewise lost from a desktop launch, so
    # route those into the log too.
    try:
        import threading as _threading
        _threading.excepthook = lambda a: log.critical(
            "unhandled exception in %s", a.thread,
            exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    except Exception:
        pass
    # The working directory is rescued in __main__, before any import
    # can trip over it; logged here because it explains failures that
    # surface much later and nowhere near the cause.
    log.info("Talkin starting (pid %s, in %s)", os.getpid(), os.getcwd())

    # Without this, GLib falls back to argv[0]'s basename for the
    # process identity — which is literally "__main__.py" when running
    # via `python -m talkin`, and that's what desktop environments show
    # as the tray icon's hover tooltip. Must run before any GTK/GLib
    # object (Tray, dialogs) is created.
    GLib.set_prgname("talkin")
    GLib.set_application_name("Talkin")

    # A source checkout isn't registered in any icon theme, so without
    # this the window manager's taskbar/dock/alt-tab falls back to a
    # generic grey icon for every window this app opens (Settings, the
    # correction popup, file dialogs) — the tray icon is set separately
    # in tray.py and unaffected either way.
    #
    # PNG, not SVG: SVG loads through a separate gdk-pixbuf loader
    # plugin (backed by librsvg) that isn't reliably present in the
    # AppImage bundle, so this raised and took the whole app down
    # before it ever got a window on screen. PNG decodes with gdk-pixbuf
    # itself, no plugin required. Wrapped regardless — a cosmetic
    # window icon must never be able to crash startup again.
    try:
        Gtk.Window.set_default_icon_from_file(
            os.path.join(cfg.ASSET_DIR, "talkin.png"))
    except GLib.GError:
        log.warning("could not load window icon", exc_info=True)

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

    import signal
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(
            GLib.PRIORITY_DEFAULT, sig, lambda: app.quit() or False)

    Gtk.main()
    log.info("Talkin quit (pid %s)", os.getpid())

    # Stop here rather than returning. Python's normal exit waits for the
    # speech model's native threads to wind down, which took about thirty
    # seconds — a tray icon that lingers half a minute after Quit reads
    # as a crash. Everything that matters (settings, history, dictionary)
    # is written to disk the moment it changes, so there is nothing left
    # to lose by leaving now.
    logging.shutdown()
    os._exit(0)
