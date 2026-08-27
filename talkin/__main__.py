# SPDX-License-Identifier: GPL-3.0-or-later
from . import config

# Must run before anything (even lazily) imports sounddevice.
config.patch_library_lookup()

# Must also run before GTK opens a display: it replaces this process.
config.prefer_x11()

from .app import main

main()
