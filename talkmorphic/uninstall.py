"""Removing Talkmorphic completely.

Deleting the AppImage is not uninstalling. It leaves behind the ~600 MB
speech model, the settings, the history and dictionary, the launcher
entry in the applications menu, and the autostart entry — so the icon
still appears, and 600 MB stays on disk with nothing to explain it.

This removes all of it, including the AppImage itself, so "remove the
program" means what it says. Everything it touches is something Talkmorphic
created; nothing else is deleted.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import os
import shutil

from .config import _WRITABLE_ROOT, launcher_path

log = logging.getLogger("talkmorphic.uninstall")

_APP_ID = "uk.co.lightmorphic.Talkmorphic"


def _applications_dir():
    return os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "applications")


def targets():
    """Everything that would be removed, as (description, path) pairs.

    Built as a list so the UI can say exactly what is about to go, and
    so a dry run is possible without deleting anything.
    """
    items = []

    data = _WRITABLE_ROOT
    if os.environ.get("APPIMAGE") and os.path.isdir(data):
        items.append(("data and speech model", data))

    apps = _applications_dir()
    for name in (_APP_ID + ".desktop", "talkmorphic.desktop"):
        path = os.path.join(apps, name)
        if os.path.exists(path):
            items.append(("menu entry", path))

    autostart = os.path.join(
        os.path.expanduser("~/.config/autostart"), "talkmorphic.desktop")
    if os.path.exists(autostart):
        items.append(("autostart entry", autostart))

    script, entry = _cleanup_paths()
    for path in (script, entry):
        if os.path.exists(path):
            items.append(("cleanup watcher", path))

    appimage = os.environ.get("APPIMAGE")
    if appimage and os.path.exists(appimage):
        items.append(("the application itself", appimage))

    return items


def total_bytes():
    """How much disk space removing everything would free."""
    total = 0
    for _label, path in targets():
        if os.path.isdir(path):
            for root, _dirs, files in os.walk(path):
                for name in files:
                    try:
                        total += os.path.getsize(os.path.join(root, name))
                    except OSError:
                        continue
        else:
            try:
                total += os.path.getsize(path)
            except OSError:
                continue
    return total


def run():
    """Remove everything. Returns a list of anything that could not go.

    The AppImage is deleted last: it is the file this process is running
    from, and on Linux that is fine — the running program keeps working
    from the open file until it exits.
    """
    problems = []
    for label, path in targets():
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            log.info("removed %s: %s", label, path)
        except OSError as exc:
            log.warning("could not remove %s (%s): %s", label, path, exc)
            problems.append("{}: {}".format(label, exc))
    return problems


# -- self-cleaning after the AppImage is deleted -----------------------
#
# Deleting the AppImage cannot run any of our code: the file is gone, so
# nothing of ours executes to tidy up, and the model, settings and menu
# entry are left behind taking ~1.7 GB. No application can solve that
# from inside itself.
#
# What it can do is leave one small watcher that runs at login, checks
# whether the AppImage it belongs to still exists, and if not removes
# everything - including itself. That makes "delete the app" mean what a
# person expects, at the cost of a 1 KB script that deletes itself the
# first time it finds its app gone.

_CLEANUP_NAME = "talkmorphic-cleanup"


def _quote(path):
    return "'" + str(path).replace("'", "'\\''") + "'"


def _cleanup_paths():
    home = os.path.expanduser("~")
    script = os.path.join(home, ".local", "bin", _CLEANUP_NAME)
    entry = os.path.join(home, ".config", "autostart",
                         _CLEANUP_NAME + ".desktop")
    return script, entry


def install_cleanup_hook():
    """Leave a login-time watcher that removes everything if we vanish."""
    script, entry = _cleanup_paths()
    appimage = os.environ.get("APPIMAGE")
    if not appimage:
        # Running from source, so there is no AppImage for a watcher to
        # watch. Any watcher still lying about is from an install that
        # has gone, and it would delete the model and settings of THIS
        # one at the next login. Take it out.
        for path in (script, entry):
            if os.path.exists(path):
                try:
                    os.remove(path)
                    log.info("removed a stale cleanup watcher: %s", path)
                except OSError as exc:
                    log.warning("could not remove %s: %s", path, exc)
        return None

    # Never watch a copy running from a temporary location. A watcher
    # points at one exact path and deletes the model, settings and
    # history when that path goes; aimed at /tmp it is a delayed
    # accident, because the path is guaranteed to disappear and the
    # deletion then hits the real install's data.
    real = os.path.realpath(appimage)
    if any(real.startswith(bad + os.sep)
           for bad in ("/tmp", "/var/tmp", "/dev/shm", "/run")):
        log.info("not installing a cleanup watcher for a temporary copy: %s",
                 real)
        return None
    apps = _applications_dir()
    autostart_entry = os.path.join(
        os.path.expanduser("~"), ".config", "autostart", "talkmorphic.desktop")

    body = (
        "#!/usr/bin/env bash\n"
        "# Installed by Talkmorphic. Does nothing while Talkmorphic is installed.\n"
        "# If its AppImage has been deleted, removes the leftovers - the\n"
        "# speech model, settings, history, menu and autostart entries -\n"
        "# and then deletes itself.\n"
        "set -u\n"
        "APPIMAGE=" + _quote(appimage) + "\n"
        "[ -e \"$APPIMAGE\" ] && exit 0\n"
        "rm -rf " + _quote(_WRITABLE_ROOT) + "\n"
        "rm -f " + _quote(os.path.join(apps, "talkmorphic.desktop")) + " "
                 + _quote(os.path.join(apps, _APP_ID + ".desktop")) + "\n"
        "rm -f " + _quote(autostart_entry) + "\n"
        "rm -f " + _quote(entry) + "\n"
        "rm -f \"$0\"\n")

    desktop = ("[Desktop Entry]\n"
               "Type=Application\n"
               "Name=Talkmorphic cleanup\n"
               "Comment=Removes Talkmorphic leftovers if the app was deleted\n"
               "Exec=" + script + "\n"
               "NoDisplay=true\n"
               "X-GNOME-Autostart-enabled=true\n")
    try:
        os.makedirs(os.path.dirname(script), exist_ok=True)
        os.makedirs(os.path.dirname(entry), exist_ok=True)
        with open(script, "w", encoding="utf-8") as f:
            f.write(body)
        os.chmod(script, 0o755)
        with open(entry, "w", encoding="utf-8") as f:
            f.write(desktop)
        log.info("installed cleanup watcher at %s", script)
        return script
    except OSError as exc:
        log.warning("could not install the cleanup watcher: %s", exc)
        return None
