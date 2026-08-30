"""What goes on the Help page: every question, grouped and searchable.

Only translation keys live here — the actual English words are in
locales/translations.csv like every other user-facing string, so the
Help page translates the same way the rest of the app does. `glyph` is
an optional talk.download_window.Glyph kind, drawn beside a
question when the same gesture already has a picture there.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

CATEGORIES = [
    ("help.cat.start", [
        ("help.q.what_is", "help.a.what_is", None),
        ("help.q.first_click", "help.a.first_click", "click"),
        ("help.q.permission", "help.a.permission", None),
        ("help.q.move_button", "help.a.move_button", None),
        ("help.q.button_missing", "help.a.button_missing", None),
        ("help.q.language", "help.a.language", None),
        ("help.q.autostart", "help.a.autostart", "toggle"),
    ]),
    ("help.cat.teach", [
        ("help.q.teach_how", "help.a.teach_how", "teach"),
        ("help.q.teach_no_highlight", "help.a.teach_no_highlight", None),
        ("help.q.dictionary_toggle", "help.a.dictionary_toggle", "toggle"),
        ("help.q.dictionary_manage", "help.a.dictionary_manage", None),
    ]),
    ("help.cat.sounds", [
        ("help.q.sounds_toggle", "help.a.sounds_toggle", "toggle"),
        ("help.q.sound_theme", "help.a.sound_theme", None),
    ]),
    ("help.cat.mic", [
        ("help.q.mic_choose", "help.a.mic_choose", None),
        ("help.q.mic_test", "help.a.mic_test", None),
    ]),
    ("help.cat.output", [
        ("help.q.paste_vs_type", "help.a.paste_vs_type", None),
        ("help.q.keep_clipboard", "help.a.keep_clipboard", "toggle"),
        ("help.q.cleanup_fillers", "help.a.cleanup_fillers", "toggle"),
        ("help.q.cleanup_dictionary", "help.a.cleanup_dictionary", "toggle"),
    ]),
    ("help.cat.history", [
        ("help.q.history_toggle", "help.a.history_toggle", "toggle"),
        ("help.q.history_manage", "help.a.history_manage", None),
    ]),
    ("help.cat.trouble", [
        ("help.q.nothing_typed", "help.a.nothing_typed", None),
        ("help.q.missed_words", "help.a.missed_words", None),
        ("help.q.wrong_words", "help.a.wrong_words", "teach"),
        ("help.q.report_bug", "help.a.report_bug", None),
    ]),
]

# The update dot and privacy/contact blocks are not plain Q&A text —
# the dot legend draws the actual coloured dots, and the contact line
# needs to stay copyable — so settings_window.py builds those two
# specially rather than from this list. Their category headers still
# live here so the search box's category order stays in one place.
UPDATES_CATEGORY = "help.cat.updates"
PRIVACY_CATEGORY = "help.cat.privacy"
CONTACT_CATEGORY = "help.cat.contact"
