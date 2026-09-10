"""Text cleanup: fillers, British spelling, personal dictionary.

Runs entirely locally on the transcript text. The dictionary maps
"what the model heard" -> "what Charlie actually means", built up via
the teach-a-word popup or the Settings page, and it runs last so that a
word taught by hand beats anything decided here.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import re

from .spelling import BRITISH

# Standalone hesitation sounds only — never words that can carry meaning.
# The "(?<!\d )" guard keeps units like "5 mm" intact.
_F = r"(?<!\d )\b(?:um+|uh+|er|erm+|ah+|hmm+|mm|mhm+)\b"


def _match_case(replacement, heard_word):
    """Give the replacement the same casing shape as what was typed."""
    if heard_word.isupper() and len(heard_word) > 1:
        return replacement.upper()
    if heard_word[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def apply_dictionary(text, entries):
    for entry in entries:
        heard, say = entry.get("heard", ""), entry.get("say", "")
        if not heard or not say:
            continue
        pattern = re.compile(
            r"\b" + re.escape(heard) + r"\b", re.IGNORECASE)
        text = pattern.sub(lambda m: _match_case(say, m.group(0)), text)
    return text


_BRITISH_RE = re.compile(
    r"\b(" + "|".join(sorted(BRITISH, key=len, reverse=True)) + r")\b",
    re.IGNORECASE)


def apply_british(text):
    """American spellings to British ones, one word at a time.

    The model has one English and spells it the American way whatever
    the accent it heard, so this is the only place the country can be
    honoured. See spelling.py for what is deliberately left alone.
    """
    return _BRITISH_RE.sub(
        lambda m: _match_case(BRITISH[m.group(0).lower()], m.group(0)), text)


def remove_fillers(text):
    # Starting a sentence, capitalise what follows: "Um, hello" -> "Hello".
    # Must run first, before the punctuation rule eats the filler alone.
    text = re.sub(
        r"(^|[.!?]\s+)" + _F + r"[,.]?\s+(\w)",
        lambda m: m.group(1) + m.group(2).upper(), text, flags=re.I)
    # Between commas, take both commas with it: "is, uh, the" -> "is the".
    text = re.sub(r",\s*" + _F + r"\s*,\s*", " ", text, flags=re.I)
    # Before punctuation, vanish cleanly: "well um." -> "well."
    text = re.sub(r"\s*" + _F + r"\s*(?=[,.!?;:])", "", text, flags=re.I)
    # Anything left standing alone.
    text = re.sub(r"(?:(?<=\s)|(?<=^))" + _F + r"[,.]?(?:\s+|$)", "",
                  text, flags=re.I)
    # Tidy artefacts: doubled spaces, space before punctuation,
    # doubled punctuation left behind by a removed filler.
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r"([,.!?;:])\1+", r"\1", text)
    return text.strip()


def clean(text, config, dictionary):
    text = text.strip()
    if not text:
        return text
    if config.get("cleanup_fillers"):
        text = remove_fillers(text)
    if config.get("dictation_language") == "en-GB":
        text = apply_british(text)
    if config.get("cleanup_dictionary") and config.get("dictionary_enabled"):
        text = apply_dictionary(text, dictionary.entries())
    # Capitalise the first letter if the model didn't.
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text
