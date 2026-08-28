# SPDX-License-Identifier: GPL-3.0-or-later
import os

# FIRST, before importing anything of ours. A process whose working
# directory has been deleted still runs, but Python's own import
# machinery stats that directory and raises "No such file or directory"
# — an error with no filename on it, arriving far from its cause. It
# happens when a copy of Talkin starts inside the temporary mount of the
# copy that launched it and that mount then disappears, and it is what
# left a fresh install unable to download its speech model.
try:
    os.getcwd()
except OSError:
    try:
        os.chdir(os.path.expanduser("~"))
    except OSError:
        os.chdir("/")

from . import config

# Must run before anything (even lazily) imports sounddevice.
config.patch_library_lookup()

# Must run before anything imports huggingface_hub or httpx: both build
# their own HTTPS clients internally and read certifi.where() the moment
# they are first used, which for the model download is inside engine.py,
# imported from app.py below.
config.patch_certificates()

# Must also run before GTK opens a display: it replaces this process.
config.prefer_x11()

from .app import main

main()
