"""Text cleanup: filler-word removal and personal-dictionary corrections.

Runs entirely locally on the transcript text. The dictionary maps
"what Parakeet heard" -> "what Charlie actually means", built up via
the correction hotkey or the Settings page.
"""

import re

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
    if config.get("cleanup_dictionary"):
        text = apply_dictionary(text, dictionary.entries())
    # Capitalise the first letter if the model didn't.
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text
