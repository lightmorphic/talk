"""Checking for, and fetching, a newer copy of the speech model.

Separate from updater.py, which is about the application itself. The
two are updated on completely different terms: the app is a 178 MB
download that changes most weeks, the model is 464 MB that has not
changed since it was published. So this never runs on its own. It
answers a button, and nothing else, which is also what keeps the
promise on the website honest: the only requests this app makes are
ones somebody asked for.

The check is a single request for the repository's current revision.
The copy on disk records which revision it came from, in refs/main, so
comparing the two is exact - no dates, no sizes, no guessing.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import json
import logging
import os
import ssl
import threading
import time
import urllib.request

from .config import MODEL_DIR
from .engine import MODEL_DIR_NAME, MODEL_NAME

log = logging.getLogger("talk.model_updater")

REPO = "Systran/faster-whisper-{}".format(MODEL_NAME)
_API = "https://huggingface.co/api/models/{}".format(REPO)


def _ssl_context():
    """The bundled certificates, same as the app updater uses."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        log.debug("no bundled certificates; using the system trust store")
        return ssl.create_default_context()


def _model_dir():
    return os.path.join(MODEL_DIR, MODEL_DIR_NAME)


def local_revision():
    """Which revision the copy on disk came from, or None if there is none."""
    path = os.path.join(_model_dir(), "refs", "main")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def installed_bytes():
    """Size of the model on disk, for showing what a re-fetch would cost."""
    total = 0
    for root, _dirs, files in os.walk(_model_dir()):
        for name in files:
            path = os.path.join(root, name)
            try:
                if not os.path.islink(path):
                    total += os.path.getsize(path)
            except OSError:
                continue
    return total


def check():
    """Ask the host for the current revision.

    Returns (state, detail). state is one of:
        "uptodate"  - the copy on disk is the current one
        "available" - the host has a different revision
        "missing"   - there is no copy on disk to compare
        "error"     - the request failed; detail says why
    """
    req = urllib.request.Request(
        _API, headers={"Accept": "application/json", "User-Agent": "Talk"})
    try:
        with urllib.request.urlopen(
                req, timeout=10, context=_ssl_context()) as resp:
            data = json.load(resp)
    except Exception as exc:
        log.warning("speech model check failed", exc_info=True)
        return "error", str(exc)

    remote = (data.get("sha") or "").strip()
    if not remote:
        return "error", "the host did not say which revision is current"

    here = local_revision()
    if here is None:
        return "missing", remote
    if here == remote:
        log.info("speech model is current (%s)", remote[:8])
        return "uptodate", remote
    log.info("a different speech model revision is available: %s -> %s",
             here[:8], remote[:8])
    return "available", remote


def download(on_progress=None):
    """Fetch the current revision over the top of the copy on disk.

    Returns True if the model loads afterwards. Progress is reported as
    a fraction, counted off the disk the same way the first-run screen
    does it, because the library gives no usable progress callback of
    its own.
    """
    from . import engine

    before = installed_bytes()
    expected = max(before, 1)

    stop = False

    def watch():
        while not stop:
            if on_progress is not None:
                grown = max(0, installed_bytes() - before)
                on_progress(min(0.99, grown / float(expected)))
            time.sleep(0.5)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        from faster_whisper import WhisperModel
        # local_files_only=False for this call alone: the whole point of
        # pressing the button is to allow one fetch.
        engine._configure_hub(offline=False)
        WhisperModel(MODEL_NAME, device="cpu", compute_type="int8",
                     download_root=MODEL_DIR, local_files_only=False)
    except Exception:
        log.exception("speech model download failed")
        return False
    finally:
        stop = True
        engine._configure_hub(offline=True)
    if on_progress is not None:
        on_progress(1.0)
    log.info("speech model updated to %s", (local_revision() or "?")[:8])
    return True
