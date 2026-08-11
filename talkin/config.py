"""Configuration, paths and flat-file storage for Talkin.

Everything lives inside the project folder: config, dictionary, history
and logs are plain JSON/JSONL files in data/ so the whole app can be
backed up, moved or exported as one folder.
"""

import json
import logging
import logging.handlers
import os
import threading

APP_NAME = "talkin"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOCALE_DIR = os.path.join(BASE_DIR, "locales")
ASSET_DIR = os.path.join(BASE_DIR, "assets")
MODEL_DIR = os.path.join(BASE_DIR, "models", "hf-cache")

CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DICT_PATH = os.path.join(DATA_DIR, "dictionary.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.jsonl")
LOG_PATH = os.path.join(DATA_DIR, "talkin.log")

SETTINGS_HOST = "127.0.0.1"
SETTINGS_PORT = 4816

DEFAULTS = {
    "language": "en",
    "hotkey": "ctrl_r",
    "mode": "hold",  # hold | toggle
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
            self._values = {**DEFAULTS, **stored}

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
    launcher = os.path.join(BASE_DIR, "scripts", "talkin.sh")
    with open(path, "w", encoding="utf-8") as f:
        f.write("[Desktop Entry]\n"
                "Type=Application\n"
                "Name=Talkin\n"
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
