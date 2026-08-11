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


def reload():
    _load()


def t(key):
    row = _table.get(key)
    if not row:
        return key
    return row.get(_language) or row.get("en") or key


def language():
    return _language


def available_languages():
    """(code, native name) for every language column in the CSV."""
    names = _table.get("language.name", {})
    return [(code, names.get(code) or code.upper()) for code in _codes]


def all_strings():
    """Resolved strings for the current language (for the web UI)."""
    return {key: (row.get(_language) or row.get("en") or key)
            for key, row in _table.items()}
