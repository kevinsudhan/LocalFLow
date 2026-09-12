"""Shared scoring helpers for the benchmark harnesses.

Word error rate over raw strings punishes a recogniser for *formatting* choices
it got right: Whisper writing "4,500" where the script said "four thousand five
hundred" is not an error, and neither is "15 March" for "the fifteenth of
March". These helpers normalise numbers, ordinals and separators first so the
metric measures recognition rather than style.
"""
from __future__ import annotations

import re

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1000, "lakh": 100_000, "million": 1_000_000}
_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
    "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15,
    "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
    "twentieth": 20, "thirtieth": 30,
}
# Words the reference and the hypothesis may legitimately differ on.
_IGNORABLE = {"the", "a", "an", "of", "rs", "inr"}


def words_to_digits(tokens: list[str]) -> list[str]:
    """Fold spelled-out numbers into digits: 'four thousand five hundred' -> '4500'."""
    out: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _ORDINALS:
            out.append(str(_ORDINALS[token]))
            index += 1
            continue
        if token not in _UNITS and token not in _SCALES:
            out.append(token)
            index += 1
            continue

        total = current = consumed = 0
        while index + consumed < len(tokens):
            word = tokens[index + consumed]
            if word in _UNITS:
                current += _UNITS[word]
            elif word in _SCALES:
                scale = _SCALES[word]
                if scale >= 1000:
                    total += max(current, 1) * scale
                    current = 0
                else:
                    current = max(current, 1) * scale
            elif word == "and" and consumed > 0:
                pass
            else:
                break
            consumed += 1
        out.append(str(total + current))
        index += max(consumed, 1)
    return out


def tokenise(text: str) -> list[str]:
    lowered = (text or "").lower()
    lowered = re.sub(r"(?<=\d)[,\s](?=\d{3}(?!\d))", "", lowered)   # 4,500 -> 4500
    lowered = re.sub(r"[^\w\s]", " ", lowered)
    tokens = [t for t in lowered.split() if t]
    tokens = [re.sub(r"^(\d+)(st|nd|rd|th)$", r"\1", t) for t in tokens]
    tokens = [t.lstrip("0") or "0" if t.isdigit() else t for t in tokens]
    tokens = words_to_digits(tokens)
    return [t for t in tokens if t not in _IGNORABLE]


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein distance over normalised words, divided by reference length."""
    ref, hyp = tokenise(reference), tokenise(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        current = [i]
        for j, h in enumerate(hyp, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (r != h)))
        previous = current
    return previous[-1] / len(ref)
