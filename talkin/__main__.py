# SPDX-License-Identifier: GPL-3.0-or-later
from . import config

# Must run before anything (even lazily) imports sounddevice.
config.patch_library_lookup()

from .app import main

main()
