"""Self-update from GitHub.

Two run modes:
 - Source checkout: a release is a git tag; updating checks the tag
   out, refreshes pip dependencies, and restarts — the full Fetch
   Terminal pattern.
 - AppImage: downloads the new AppImage to a temp file *next to* the
   running one, then atomically renames it over the original path.
   That's safe on Linux specifically because a running process keeps
   its own file open by inode — replacing what the path points to
   doesn't touch the copy already running, so there's no window where
   the file is half-written or missing. Settings and the downloaded
   speech model live outside the AppImage entirely, in a folder that
   survives every future update untouched either way.

Privacy: this module is the ONLY code in Talkin that touches the
network, it talks only to github.com, and it runs only when the
Settings page asks it to — never on a timer, never in the background.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import json
import logging
import os
import re
import subprocess
import urllib.request

from . import __version__
from .config import BASE_DIR, DATA_DIR

log = logging.getLogger("talkin.updater")

REPO = "lightmorphic/talkin"
RELEASES_PAGE = "https://github.com/{}/releases/latest".format(REPO)
_ASSET_URL = "https://github.com/{}/releases/download/{{}}/Talkin-x86_64.AppImage".format(REPO)
PREVIOUS_PATH = os.path.join(DATA_DIR, "previous-version.txt")
# A real build is ~90-120MB; anything wildly smaller means the
# download failed partway or GitHub served an error page instead.
_MIN_APPIMAGE_SIZE = 20_000_000


def is_packaged():
    return bool(os.environ.get("APPIMAGE"))


def _parse(tag):
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag.strip())
    return tuple(int(p) for p in match.groups()) if match else None


def _git(*args, timeout=30):
    return subprocess.run(
        ["git", "-C", BASE_DIR, *args],
        capture_output=True, text=True, timeout=timeout)


def _latest_release_tag():
    req = urllib.request.Request(
        "https://api.github.com/repos/{}/releases/latest".format(REPO),
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "Talkin"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
    except Exception as e:
        log.warning("release check failed", exc_info=True)
        return None, str(e)
    return data.get("tag_name"), None


def _latest_source_tag():
    try:
        result = _git("fetch", "--tags", "--quiet", "origin")
    except Exception as e:
        log.warning("update check failed", exc_info=True)
        return None, str(e)
    if result.returncode != 0:
        detail = result.stderr.strip()
        log.warning("update check failed: %s", detail)
        return None, detail or "git fetch failed"
    tags = _git("tag", "--list", "v*").stdout.split()
    versions = sorted(v for v in (_parse(t) for t in tags) if v)
    if not versions:
        return None, "no version tags found"
    return "v{}.{}.{}".format(*versions[-1]), None


def check():
    """Compare the running version with the newest release on GitHub."""
    latest_tag, error = (_latest_release_tag() if is_packaged()
                         else _latest_source_tag())
    if latest_tag is None:
        return {"state": "error", "detail": error or "unknown error"}
    latest = _parse(latest_tag)
    current = _parse("v" + __version__) or (0, 0, 0)
    if latest and latest > current:
        return {"state": "available", "latest": latest_tag,
                "current": __version__, "packaged": is_packaged(),
                "download_url": RELEASES_PAGE}
    return {"state": "up-to-date", "current": __version__}


def apply(tag, on_progress=None):
    """Update to `tag` and report success. Caller restarts on True.

    on_progress, when given, is called with a 0..1 fraction as the
    AppImage downloads (source-checkout updates have no comparable
    progress to report, so it's simply never called in that mode).
    """
    if not _parse(tag):
        return False
    if is_packaged():
        return _apply_appimage(tag, on_progress)
    return _apply_source(tag)


def _apply_appimage(tag, on_progress=None):
    appimage_path = os.environ.get("APPIMAGE")
    if not appimage_path:
        return False
    tmp_path = appimage_path + ".new"
    try:
        req = urllib.request.Request(
            _ASSET_URL.format(tag), headers={"User-Agent": "Talkin"})
        with urllib.request.urlopen(req, timeout=300) as resp, \
                open(tmp_path, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            written = 0
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)
                if on_progress and total:
                    on_progress(min(1.0, written / total))
        if os.path.getsize(tmp_path) < _MIN_APPIMAGE_SIZE:
            raise ValueError("downloaded file is implausibly small")
        os.chmod(tmp_path, 0o755)
        os.replace(tmp_path, appimage_path)
    except Exception:
        log.exception("appimage self-update to %s failed", tag)
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return False
    log.info("updated to %s", tag)
    return True


def _apply_source(tag):
    with open(PREVIOUS_PATH, "w", encoding="utf-8") as f:
        f.write("v" + __version__ + "\n")
    result = _git("checkout", "--quiet", "tags/" + tag)
    if result.returncode != 0:
        log.error("checkout %s failed: %s", tag, result.stderr.strip())
        return False
    pip = os.path.join(BASE_DIR, ".venv", "bin", "pip")
    req = os.path.join(BASE_DIR, "requirements.txt")
    if os.path.exists(req):
        dep = subprocess.run([pip, "install", "-q", "-r", req],
                             capture_output=True, text=True, timeout=600)
        if dep.returncode != 0:
            log.error("dependency refresh failed: %s", dep.stderr[-500:])
    log.info("updated to %s", tag)
    return True


def rollback():
    """Return a source checkout to the version before the last update."""
    if is_packaged():
        return False
    try:
        with open(PREVIOUS_PATH, "r", encoding="utf-8") as f:
            tag = f.read().strip()
    except OSError:
        return False
    if not _parse(tag):
        return False
    result = _git("checkout", "--quiet", "tags/" + tag)
    if result.returncode == 0:
        log.info("rolled back to %s", tag)
        return True
    return False
