"""Configuration, paths and flat-file storage for Talkin.

Everything lives inside the project folder: config, dictionary, history
and logs are plain JSON/JSONL files in data/ so the whole app can be
backed up, moved or exported as one folder.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import json
import logging
import logging.handlers
import os
import threading

log = logging.getLogger("talkin.config")

APP_NAME = "talkin"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# When packaged as an AppImage, BASE_DIR resolves inside that version's
# read-only, throwaway squashfs mount. Anything Talkin needs to WRITE —
# its own settings, and the downloaded speech model, which must survive
# every future update without re-downloading 600 MB — lives instead in
# one persistent per-user folder outside the bundle. A source checkout
# has no such throwaway mount, so it keeps everything in the repo, as
# a single self-contained folder.
if os.environ.get("APPIMAGE"):
    _WRITABLE_ROOT = os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "talkin")
else:
    _WRITABLE_ROOT = BASE_DIR

LOCALE_DIR = os.path.join(BASE_DIR, "locales")
ASSET_DIR = os.path.join(BASE_DIR, "assets")
DATA_DIR = os.path.join(_WRITABLE_ROOT, "data")
MODEL_DIR = os.path.join(_WRITABLE_ROOT, "models", "hf-cache")

CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DICT_PATH = os.path.join(DATA_DIR, "dictionary.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.jsonl")
# TALKIN_LOG_DIR redirects only the log, leaving settings, history and
# the 600 MB model cache where they are. It exists so a log can be written
# somewhere a helper can actually read when diagnosing a fault, without
# disturbing the install.
_LOG_DIR = os.environ.get("TALKIN_LOG_DIR") or DATA_DIR
LOG_PATH = os.path.join(_LOG_DIR, "talkin.log")

DEFAULTS = {
    "language": "en",
    "injection": "paste",  # paste | type
    "mic": "default",
    "cleanup_fillers": True,
    "cleanup_dictionary": True,
    "history_enabled": True,
    "autostart": True,
    # The floating record button. Talkin has no keyboard shortcuts —
    # a compositor's press and release signals lost dictations, and a
    # click has no such signal to get wrong — so this and the tray icon
    # are the only ways in.
    "float_button": True,
    # A short blip when the microphone opens and another when it closes.
    # The button changes colour too, but nobody is looking at the button
    # while dictating — they are looking at what they are dictating into.
    "sounds": True,
    # Cleared until the first-run notice has been shown once. The notice
    # carries the permission step, which silently breaks everything if
    # skipped, so it must appear even when the model is already cached.
    "first_run_seen": False,
    # Wayland only: the RemoteDesktop portal's permission token. Storing it
    # turns "approve input access on every launch" into a one-off prompt.
    # Not user-facing and never shown in Settings.
    "wayland_restore_token": "",
}

_lock = threading.RLock()


def _read_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return fallback


# Everything Talkin keeps is private to the person who dictated it: the
# history is the text of what they said, and config.json holds the
# portal permission token. Left at the default mode both are readable by
# every other account on the machine, so the directory is 0700 and the
# files inside it 0600.
_DIR_MODE = 0o700
_FILE_MODE = 0o600


def _private_dir(path):
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, _DIR_MODE)
    except OSError:
        log.debug("could not tighten permissions on %s", path)
    return path


def _private_file(path):
    try:
        os.chmod(path, _FILE_MODE)
    except OSError:
        log.debug("could not tighten permissions on %s", path)
    return path


def _write_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    _private_file(tmp)
    os.replace(tmp, path)


class Config:
    """Thread-safe view of config.json with defaults filled in."""

    def __init__(self):
        _private_dir(DATA_DIR)
        with _lock:
            stored = _read_json(CONFIG_PATH, {})
            # Keep only keys DEFAULTS still defines — settings removed in
            # an update (like the old "hotkey"/"mode" pair) don't linger
            # forever in an upgraded install's config.json.
            self._values = {**DEFAULTS,
                            **{k: v for k, v in stored.items() if k in DEFAULTS}}

    def get(self, key):
        with _lock:
            return self._values.get(key, DEFAULTS.get(key))

    def all(self):
        with _lock:
            return dict(self._values)

    def update(self, changes):
        with _lock:
            for key in DEFAULTS:
                if key in changes:
                    self._values[key] = changes[key]
            _write_json(CONFIG_PATH, self._values)


class Dictionary:
    """The personal dictionary: a list of {heard, say} pairs."""

    def __init__(self):
        _private_dir(DATA_DIR)

    def entries(self):
        with _lock:
            data = _read_json(DICT_PATH, {})
            return list(data.get("entries", []))

    def _save(self, entries):
        _write_json(DICT_PATH, {"talkin_dictionary": 1, "entries": entries})

    def add(self, heard, say):
        heard, say = heard.strip(), say.strip()
        if not heard or not say:
            return
        with _lock:
            entries = [e for e in self.entries()
                       if e["heard"].lower() != heard.lower()]
            entries.append({"heard": heard, "say": say})
            self._save(entries)

    def remove(self, heard):
        with _lock:
            entries = [e for e in self.entries()
                       if e["heard"].lower() != heard.lower()]
            self._save(entries)

    def replace_all(self, entries):
        cleaned = []
        for e in entries:
            heard = str(e.get("heard", "")).strip()
            say = str(e.get("say", "")).strip()
            if heard and say:
                cleaned.append({"heard": heard, "say": say})
        with _lock:
            self._save(cleaned)


class History:
    """Append-only local dictation history (JSONL, newest last)."""

    def __init__(self, config):
        self.config = config
        _private_dir(DATA_DIR)

    def add(self, raw, clean):
        if not self.config.get("history_enabled"):
            return
        import time
        entry = {"ts": int(time.time()), "raw": raw, "clean": clean}
        with _lock:
            new = not os.path.exists(HISTORY_PATH)
            with open(HISTORY_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            if new:
                _private_file(HISTORY_PATH)

    def entries(self, limit=200):
        with _lock:
            try:
                with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError:
                return []
        out = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        out.reverse()
        return out

    def clear(self):
        with _lock:
            try:
                os.remove(HISTORY_PATH)
            except OSError:
                pass

    def stats(self):
        entries = self.entries(limit=100000)
        words = sum(len(e.get("clean", "").split()) for e in entries)
        return {"dictations": len(entries), "words": words}


def patch_library_lookup():
    """Work around ctypes.util.find_library inside the AppImage.

    sounddevice locates PortAudio via find_library(), which on Linux
    normally shells out to ldconfig — and ldconfig only knows about
    libraries actually installed on the host, never anything bundled
    inside the AppImage. Point it at our bundled copy directly so
    sounddevice's own Linux code path (which has no other fallback)
    finds it. A no-op outside the AppImage.
    """
    appdir = os.environ.get("APPDIR")
    if not appdir:
        return
    import ctypes.util
    bundled = {"portaudio": os.path.join(appdir, "usr", "lib", "libportaudio.so.2")}
    original = ctypes.util.find_library

    def find_library(name):
        path = bundled.get(name)
        if path and os.path.exists(path):
            return path
        return original(name)

    ctypes.util.find_library = find_library


def prefer_x11():
    """Re-launch under XWayland, before any window has been created.

    Wayland deliberately gives applications no way to position a window
    or to put one in front. That is right for the desktop and wrong for a
    small button that has to sit somewhere predictable and stay visible:
    it opens in the middle of the screen, over whatever is there.
    XWayland restores both, at the cost of slightly softer edges on a
    scaled display.

    The AppImage already runs this way — its GTK bundle sets the backend
    before we get a say — so this only affects running from source, where
    the two would otherwise behave differently and only one of them ever
    gets tested.

    It has to happen before GTK opens a display connection, so it
    replaces the process rather than changing anything in it.
    """
    if os.environ.get("GDK_BACKEND"):
        return                      # someone has already chosen
    if not os.environ.get("WAYLAND_DISPLAY"):
        return                      # already on X11
    if not os.environ.get("DISPLAY"):
        return                      # no XWayland to fall back to
    import sys
    os.environ["GDK_BACKEND"] = "x11"
    argv = [sys.executable]
    if sys.flags.no_site:
        argv.append("-S")
    argv += ["-m", "talkin"] + sys.argv[1:]
    try:
        os.execv(sys.executable, argv)
    except OSError:
        # Carry on natively rather than not starting at all.
        os.environ.pop("GDK_BACKEND", None)


def launcher_path():
    """The command that relaunches Talkin exactly as it's running now."""
    appimage = os.environ.get("APPIMAGE")
    return appimage if appimage else os.path.join(BASE_DIR, "scripts", "talkin.sh")


def desktop_exec(path):
    """A path as the Exec value of a desktop entry.

    Quoted, because an AppImage lives wherever its owner put it and
    "~/My Apps/talkin.appimage" is an ordinary enough place. Unquoted,
    the launcher reads that as two arguments and the entry silently does
    nothing.
    """
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    return '"{}"'.format(escaped)


def set_autostart(enabled):
    """Write or remove the desktop-autostart entry for Talkin."""
    autostart_dir = os.path.expanduser("~/.config/autostart")
    path = os.path.join(autostart_dir, "talkin.desktop")
    if not enabled:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    os.makedirs(autostart_dir, exist_ok=True)
    launcher = launcher_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write("[Desktop Entry]\n"
                "Type=Application\n"
                "Name=Talkin\n"
                "Comment=Private on-device dictation\n"
                f"Exec={desktop_exec(launcher)}\n"
                f"Icon={os.path.join(ASSET_DIR, 'talkin-idle.svg')}\n"
                "StartupWMClass=talkin\n"
                "X-GNOME-Autostart-enabled=true\n")


def setup_logging():
    _private_dir(DATA_DIR)
    _private_dir(_LOG_DIR)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
    _private_file(LOG_PATH)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return logging.getLogger(APP_NAME)
