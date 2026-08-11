"""Self-update from GitHub.

Two run modes:
 - Source checkout: a release is a git tag; updating checks the tag
   out, refreshes pip dependencies, and restarts — the full Fetch
   Terminal pattern.
 - AppImage: there's no git repo to check out, and safely replacing a
   running AppImage's own file from inside itself is exactly the kind
   of thing that goes wrong in a way that loses the user's only copy.
   So in this mode Talkin only ever CHECKS for a newer release (via
   the GitHub Releases API) and hands back the download page for the
   user to grab the new file themselves. Nothing is lost either way —
   settings and the downloaded speech model live outside the AppImage,
   in a folder that survives every future update untouched.

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
PREVIOUS_PATH = os.path.join(DATA_DIR, "previous-version.txt")


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
    except Exception:
        log.warning("release check failed", exc_info=True)
        return None
    return data.get("tag_name")


def _latest_source_tag():
    result = _git("fetch", "--tags", "--quiet", "origin")
    if result.returncode != 0:
        log.warning("update check failed: %s", result.stderr.strip())
        return None
    tags = _git("tag", "--list", "v*").stdout.split()
    versions = sorted(v for v in (_parse(t) for t in tags) if v)
    return "v{}.{}.{}".format(*versions[-1]) if versions else None


def check():
    """Compare the running version with the newest release on GitHub."""
    latest_tag = (_latest_release_tag() if is_packaged()
                  else _latest_source_tag())
    if latest_tag is None:
        return {"state": "error"}
    latest = _parse(latest_tag)
    current = _parse("v" + __version__) or (0, 0, 0)
    if latest and latest > current:
        return {"state": "available", "latest": latest_tag,
                "current": __version__, "packaged": is_packaged(),
                "download_url": RELEASES_PAGE}
    return {"state": "up-to-date", "current": __version__}


def apply(tag):
    """Move a source checkout to `tag`, refresh deps, report success.

    Never called in AppImage mode — see the module docstring.
    """
    if is_packaged() or not _parse(tag):
        return False
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
