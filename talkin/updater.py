"""Self-update from GitHub, the Fetch Terminal way, adapted for git.

A release is a git tag (v1.2.3) on the GitHub repo. Checking compares
the newest remote tag with the running version; updating checks the
tag out, refreshes dependencies and restarts. The previous version is
remembered so a bad update can be rolled back with one command.

Privacy: this module is the ONLY code in Talkin that touches the
network, it talks only to github.com, and it runs only when the
Settings page asks it to — never on a timer, never in the background.
"""

import logging
import os
import re
import subprocess

from . import __version__
from .config import BASE_DIR, DATA_DIR

log = logging.getLogger("talkin.updater")

REPO_URL = "https://github.com/lightmorphic/talkin"
PREVIOUS_PATH = os.path.join(DATA_DIR, "previous-version.txt")


def _git(*args, timeout=30):
    return subprocess.run(
        ["git", "-C", BASE_DIR, *args],
        capture_output=True, text=True, timeout=timeout)


def _parse(tag):
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag.strip())
    return tuple(int(p) for p in match.groups()) if match else None


def check():
    """Fetch tags from GitHub and compare with the running version."""
    result = _git("fetch", "--tags", "--quiet", "origin")
    if result.returncode != 0:
        log.warning("update check failed: %s", result.stderr.strip())
        return {"state": "error"}
    tags = _git("tag", "--list", "v*").stdout.split()
    versions = sorted(v for v in (_parse(t) for t in tags) if v)
    if not versions:
        return {"state": "error"}
    latest = versions[-1]
    current = _parse("v" + __version__) or (0, 0, 0)
    latest_tag = "v{}.{}.{}".format(*latest)
    if latest > current:
        return {"state": "available", "latest": latest_tag,
                "current": __version__}
    return {"state": "up-to-date", "current": __version__}


def apply(tag):
    """Move to `tag`, refresh dependencies, and report success.

    The caller restarts the app afterwards. The version we're leaving
    is written down first so rollback is always one step away.
    """
    if not _parse(tag):
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
    """Return to the version recorded before the last update."""
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
