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
LOG_PATH = os.path.join(DATA_DIR, "talkin.log")

SETTINGS_HOST = "127.0.0.1"
SETTINGS_PORT = 4816

DEFAULTS = {
    "language": "en",
    "hotkey_hold": "ctrl_r",
    "hotkey_toggle": "",
    "correction_hotkey": "ctrl+alt+c",
    "injection": "paste",  # paste | type
    "mic": "default",
    "cleanup_fillers": True,
    "cleanup_dictionary": True,
    "history_enabled": True,
    "autostart": True,
}

_lock = threading.RLock()


def _read_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return fallback


def _write_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class Config:
    """Thread-safe view of config.json with defaults filled in."""

    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
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
        os.makedirs(DATA_DIR, exist_ok=True)

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
        os.makedirs(DATA_DIR, exist_ok=True)

    def add(self, raw, clean):
        if not self.config.get("history_enabled"):
            return
        import time
        entry = {"ts": int(time.time()), "raw": raw, "clean": clean}
        with _lock:
            with open(HISTORY_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

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


def launcher_path():
    """The command that relaunches Talkin exactly as it's running now."""
    appimage = os.environ.get("APPIMAGE")
    return appimage if appimage else os.path.join(BASE_DIR, "scripts", "talkin.sh")


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
                "Name=Lightmorphic Talkin\n"
                "Comment=Private on-device dictation\n"
                f"Exec={launcher}\n"
                f"Icon={os.path.join(ASSET_DIR, 'talkin-idle.svg')}\n"
                "X-GNOME-Autostart-enabled=true\n")


def setup_logging():
    os.makedirs(DATA_DIR, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return logging.getLogger(APP_NAME)
