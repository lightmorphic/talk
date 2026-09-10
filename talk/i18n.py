"""Dead-simple translation system: one human-editable CSV.

All visible text lives in locales/translations.csv. Column one is the
string key; every other column is a language (header row holds the
code, the "language.name" row holds its native name). To translate the
app: add a column, fill the cells — in a spreadsheet or by handing the
file to an AI. Blank cells fall back to English, so a half-finished
column still works.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import csv
import os

from .config import LOCALE_DIR

CSV_PATH = os.path.join(LOCALE_DIR, "translations.csv")

_table = {}      # key -> {lang: text}
_codes = []      # language codes in column order
_language = "en"


def _load():
    global _table, _codes
    _table, _codes = {}, []
    try:
        with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
    except (OSError, csv.Error):
        return
    if not rows or len(rows[0]) < 2 or rows[0][0] != "key":
        return
    _codes = [c.strip() for c in rows[0][1:] if c.strip()]
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        key = row[0].strip()
        _table[key] = {
            code: row[i + 1].strip() if i + 1 < len(row) else ""
            for i, code in enumerate(_codes)
        }


def set_language(code):
    global _language
    if not _table:
        _load()
    _language = code if code in _codes else "en"


def t(key):
    row = _table.get(key)
    if not row:
        return key
    return row.get(_language) or row.get("en") or key


def available_languages():
    """(code, native name) for every language column in the CSV.

    Loads the table if nothing has yet, so a caller that asks before the
    first translated string gets the languages rather than an empty
    list — which is what an untouched language picker used to show.
    """
    if not _table:
        _load()
    names = _table.get("language.name", {})
    return [(code, names.get(code) or code.upper()) for code in _codes]


def dictation_languages():
    """As above, but English appears twice, once per country.

    There is only one English in the model, and only one in the
    interface. What differs is the spelling of the words it types, and
    that is ours to decide - so the choice lives here, in the list
    someone already goes to when choosing what to dictate in, rather
    than in a switch on some other page.
    """
    out = []
    for code, name in available_languages():
        if code == "en":
            out.append(("en-GB", "English (British)"))
            out.append(("en-US", "English (American)"))
        else:
            out.append((code, name))
    return out
