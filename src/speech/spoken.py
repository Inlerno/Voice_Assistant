"""The form in which a spoken command reaches the assistant: lower case words and numbers without punctuation.

Vosk gives its text in this form, the text of Whisper and a typed phrase are brought to it (see to_spoken and
src/core/remote.py), so one parser understands all of them.
"""
import re

# Everything that is not a letter, a digit or a space is dropped, except a colon between digits (7:30) and a point or a
# comma between digits (1,5): the number would fall apart without them
_SIGNS = re.compile(r"[^\w\s:.,]|(?<!\d)[:.,]|[:.,](?!\d)")
_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d)")


def strip_signs(text):
    """The text in lower case without punctuation; "1,5" becomes "1.5" and "7:30" stays as it is."""
    text = _DECIMAL_COMMA.sub(".", _SIGNS.sub(" ", (text or "").lower()))
    return re.sub(r"\s+", " ", text).strip()


def to_spoken(text):
    """A phrase of Whisper as a spoken command arrives from Vosk (ё is written as е, as Vosk does)."""
    return strip_signs((text or "").lower().replace("ё", "е"))
