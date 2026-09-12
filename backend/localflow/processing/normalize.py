"""Stage 1 - normalisation.

Cleans up the mechanical artefacts of speech recognition (spacing, duplicated
punctuation, stutters) and resolves *explicitly spoken* punctuation such as
"comma" or "new paragraph".  Nothing here changes meaning.
"""
from __future__ import annotations

import re
import unicodedata

from . import protect

# Spoken punctuation the user may dictate literally.
SPOKEN_PUNCT: dict[str, str] = {
    "comma": ",",
    "period": ".",
    "full stop": ".",
    "question mark": "?",
    "exclamation mark": "!",
    "exclamation point": "!",
    "colon": ":",
    "semicolon": ";",
    "semi colon": ";",
    "open paren": "(",
    "open parenthesis": "(",
    "close paren": ")",
    "close parenthesis": ")",
    # "open quote" / "close quote" are handled by processing.quotes,
    # which needs to see the pair to wrap and punctuate the body.
    "ellipsis": "…",
    "dot dot dot": "…",
    "hyphen": "-",
    "dash": "-",
    "em dash": "—",
    "ampersand": "&",
    "at sign": "@",
    "hash sign": "#",
    "percent sign": "%",
}

SPOKEN_BREAKS: dict[str, str] = {
    "new paragraph": "\n\n",
    "new line": "\n",
    "newline": "\n",
    "next line": "\n",
    "line break": "\n",
    "paragraph break": "\n\n",
    "tab": "\t",
}

# Words that, immediately before a punctuation word, prove it is being *talked
# about* rather than dictated: "the comma is missing", "a dash of salt".
_DETERMINERS = {
    "a", "an", "the", "this", "that", "these", "those", "any", "some", "no",
    "every", "each", "another", "one", "your", "my", "its", "his", "her", "their",
}
_FOLLOWERS = {"is", "was", "are", "were", "key", "button", "character", "symbol", "separated"}


def normalize_unicode(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace(" ", " ").replace("​", "")
    return text


def apply_spoken_breaks(text: str) -> str:
    """Turn "new paragraph" / "new line" into real breaks.

    The surrounding spaces and any comma the recogniser attached to the phrase
    are absorbed, so "update, new paragraph, I will" becomes "update\n\nI will"
    rather than leaving orphaned punctuation behind.
    """
    for phrase, replacement in sorted(SPOKEN_BREAKS.items(), key=lambda kv: -len(kv[0])):
        pattern = re.compile(
            r"[ \t]*,?[ \t]*(?<![\w])" + re.escape(phrase) + r"(?![\w])[ \t]*,?[ \t]*",
            re.IGNORECASE,
        )
        text = pattern.sub(replacement, text)
    return text


def apply_spoken_punctuation(text: str) -> str:
    """Replace dictated punctuation words, but only where they are commands."""

    def replace(match: re.Match) -> str:
        phrase = match.group(0)
        start, end = match.span()
        before = text[:start].rstrip()
        after = text[end:].lstrip()
        prev_word = re.findall(r"[\w']+$", before)
        next_word = re.findall(r"^[\w']+", after)
        if prev_word and prev_word[-1].lower() in _DETERMINERS:
            return phrase
        if next_word and next_word[0].lower() in _FOLLOWERS:
            return phrase
        return SPOKEN_PUNCT[phrase.lower()]

    keys = sorted(SPOKEN_PUNCT, key=len, reverse=True)
    pattern = re.compile(r"(?<![\w])(" + "|".join(re.escape(k) for k in keys) + r")(?![\w])",
                         re.IGNORECASE)
    return pattern.sub(replace, text)


_SAFE_TO_COLLAPSE = {
    "the", "a", "an", "i", "to", "of", "and", "is", "it", "that", "we", "you",
    "he", "she", "they", "in", "on", "for", "with", "was", "but", "so", "do",
}
# Phrases short enough to be a false start rather than deliberate emphasis.
_PHRASE_REPEAT = tuple(
    re.compile(r"\b((?:[\w']+\s+){" + str(n - 1) + r"}[\w']+)\s+\1\b", re.IGNORECASE)
    for n in (3, 2)
)


def collapse_repeats(text: str) -> str:
    """Remove stutters and false starts.

    Single words are only collapsed when they are function words, because
    genuine repetition ("very very good", "had had enough") carries meaning.
    Short *phrases* are collapsed more readily - "let us let us do it" is a
    stumble, and nobody repeats a two-word phrase on purpose mid-sentence.
    """

    def single(match: re.Match) -> str:
        word = match.group(1)
        return word if word.lower() in _SAFE_TO_COLLAPSE else match.group(0)

    def phrase(match: re.Match) -> str:
        words = match.group(1).split()
        # A long word in the phrase suggests real content, not a stumble.
        if any(len(word) > 9 for word in words):
            return match.group(0)
        return match.group(1)

    for pattern in _PHRASE_REPEAT:
        text = pattern.sub(phrase, text)
    return re.sub(r"\b([\w']+)(\s+\1)\b", single, text, flags=re.IGNORECASE)


_HOUR_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_MINUTE_WORDS = {
    "oh five": 5, "five": 5, "ten": 10, "fifteen": 15, "twenty": 20,
    "twenty five": 25, "thirty": 30, "thirty five": 35, "forty": 40,
    "forty five": 45, "fifty": 50, "fifty five": 55,
}
_TIME_PREPS = r"(?:at|by|around|before|after|until|till|from|for)"
_SPOKEN_TIME_RE = re.compile(
    r"(?<![\w])(?P<prep>" + _TIME_PREPS + r")\s+"
    r"(?P<hour>" + "|".join(_HOUR_WORDS) + r"|\d{1,2})\s+"
    r"(?P<minute>" + "|".join(sorted(_MINUTE_WORDS, key=len, reverse=True)) + r")"
    r"(?![\w])",
    re.IGNORECASE,
)


def normalize_spoken_times(text: str) -> str:
    """Turn "at six thirty" into "at 6:30".

    Anchored on a time preposition, because without one "six thirty" could be
    a quantity. That anchor is what keeps "I need six thirty-litre drums"
    from being mangled.
    """

    def replace(match: re.Match) -> str:
        hour_text = match.group("hour").lower()
        hour = _HOUR_WORDS.get(hour_text, None)
        if hour is None:
            try:
                hour = int(hour_text)
            except ValueError:
                return match.group(0)
        if not 1 <= hour <= 23:
            return match.group(0)
        minute = _MINUTE_WORDS[match.group("minute").lower()]
        return f"{match.group('prep')} {hour}:{minute:02d}"

    return _SPOKEN_TIME_RE.sub(replace, text)


def fix_spacing(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([,.;:!?%…])", r"\1", text)
    text = re.sub(r"([,;:])(?=[^\s\d])", r"\1 ", text)
    text = re.sub(r"([.!?])(?=[A-Za-z])", r"\1 ", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    # A run of dots is one pause, not several sentence ends.
    text = re.sub(r"\.{2,}", "…", text)
    text = re.sub(r"([!?])\1{1,}", r"\1", text)
    text = re.sub(r",{2,}", ",", text)
    # Only horizontal whitespace collapses around newlines; a blank line is
    # a paragraph break and has to survive.
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_bracketed_noise(text: str) -> str:
    """Drop Whisper's non-speech annotations such as [BLANK_AUDIO] or (music)."""
    text = re.sub(r"\[(?:[A-Z_ ]{3,}|[^\]]{0,40}(?:music|silence|noise|applause)[^\]]{0,40})\]",
                  " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\((?:[^)]{0,30}(?:music|silence|inaudible|laughter)[^)]{0,30})\)",
                  " ", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*[♪♫]+\s*$", "", text)
    return text


def normalize(text: str, spoken_punctuation: bool = True) -> str:
    """Clean up recogniser artefacts without touching meaning.

    URLs, paths and identifiers are masked for the duration: the spacing rules
    below assume prose and would otherwise turn "https://example.com/api" into
    "https: //example. Com/api".
    """
    if not text:
        return ""
    text = normalize_unicode(text)
    text = strip_bracketed_noise(text)
    masked, literals = protect.mask(text)
    if spoken_punctuation:
        masked = apply_spoken_breaks(masked)
        masked = apply_spoken_punctuation(masked)
    masked = normalize_spoken_times(masked)
    masked = collapse_repeats(masked)
    masked = fix_spacing(masked)
    return protect.unmask(masked, literals)


def is_effectively_empty(text: str) -> bool:
    stripped = re.sub(r"[^\w]", "", text or "")
    return len(stripped) == 0
