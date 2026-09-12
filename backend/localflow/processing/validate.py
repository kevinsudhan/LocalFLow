"""Stage 9 - validation.

The LLM stage is optional and must never be trusted blindly.  Every model
output is checked against the text that went in; anything that looks like a
hallucination, an answer, a refusal or a chat pleasantry is rejected and the
deterministic result is used instead.

This is what makes it safe to run a general-purpose chat model as a dictation
editor: a bad edit costs nothing because it is discarded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Openers that betray a model answering rather than editing.
_ANSWER_OPENERS = re.compile(
    r"^\s*(?:sure|certainly|of course|absolutely|yes|no problem|okay|ok|got it|"
    r"understood|happy to|i'?(?:ll|ve| will| have| can| am)|let me|here(?:'s| is| are)|"
    r"i'?d be happy|as an ai|i cannot|i can't|i'?m sorry|sorry,? (?:but|i))\b",
    re.IGNORECASE,
)
# Phrases a model uses to talk *about* the edit.
_META = re.compile(
    r"\b(?:the (?:corrected|cleaned|revised|final|edited) (?:text|version|transcript)|"
    r"here(?:'s| is) the (?:corrected|cleaned|revised|final)|"
    r"i (?:have )?(?:corrected|cleaned|revised|fixed|removed|rewrote)|"
    r"note:|explanation:|output:|transcript:|let me know if)\b",
    re.IGNORECASE,
)
_NUMBER_RUN = re.compile(r"\d+")
_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9]{1,}\b")
_URLISH = re.compile(r"\b(?:https?://\S+|www\.\S+|[\w.-]+@[\w.-]+\.\w+|[A-Za-z]:\\\S+|/\S+/\S+)")
_CORRECTION_CUE = re.compile(
    r"\b(?:actually|scratch that|i meant|no wait|wait no|make that|or rather|i mean)\b",
    re.IGNORECASE,
)


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""
    detail: str = ""
    warnings: list[str] = field(default_factory=list)


_THOUSANDS = re.compile(r"(?<=\d)[, \s](?=\d{3}(?!\d))")
_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
    "nineteen": "19", "twenty": "20", "thirty": "30", "forty": "40",
    "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80", "ninety": "90",
    "hundred": "100", "thousand": "1000", "million": "1000000",
    "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5",
    "sixth": "6", "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10",
    "fifteenth": "15", "twentieth": "20",
}


def _digit_runs(text: str) -> list[str]:
    """Digit groups, with thousands separators collapsed.

    "4,500" and "4500" are the same number written two ways; a model that
    reformats one into the other has not dropped anything.
    """
    return _NUMBER_RUN.findall(_THOUSANDS.sub("", text or ""))


def _spoken_numbers(text: str) -> set[str]:
    """Digits a spelled-out number in the source could legitimately become."""
    words = re.findall(r"[a-z]+", (text or "").lower())
    return {_NUMBER_WORDS[word] for word in words if word in _NUMBER_WORDS}


def _spoken_number_words(text: str) -> list[str]:
    """Number words actually written out in the text, in order."""
    words = re.findall(r"[a-z]+", (text or "").lower())
    return [word for word in words if word in _NUMBER_WORDS]

def _missing(items: list[str], haystack: str) -> list[str]:
    lowered = haystack.lower()
    missing: list[str] = []
    for item in items:
        if item.lower() not in lowered:
            missing.append(item)
    return missing


def validate_llm_output(
    original: str,
    produced: str,
    max_ratio: float = 2.2,
    min_ratio: float = 0.35,
    protected_terms: list[str] | None = None,
    allow_removals: bool = False,
    style_allows_reordering: bool = False,
) -> ValidationResult:
    """Decide whether the model's rewrite may be used."""
    text = (produced or "").strip()
    source = (original or "").strip()
    if not source:
        return ValidationResult(True)
    if not text:
        return ValidationResult(False, "empty", "The model returned nothing.")

    # 1. Size sanity.
    ratio = len(text) / max(1, len(source))
    if ratio > max_ratio:
        return ValidationResult(
            False, "too_long", f"Output is {ratio:.1f}x the input - likely elaboration."
        )
    if ratio < min_ratio and not style_allows_reordering:
        return ValidationResult(
            False, "too_short", f"Output is {ratio:.2f}x the input - content was dropped."
        )

    # 2. Chat behaviour.
    if _ANSWER_OPENERS.match(text) and not _ANSWER_OPENERS.match(source):
        return ValidationResult(False, "answered", "The model replied instead of editing.")
    if _META.search(text) and not _META.search(source):
        return ValidationResult(False, "commentary", "The model described its edits.")
    if text.count("\n\n") > source.count("\n\n") + 3:
        return ValidationResult(False, "restructured", "The model added unexpected structure.")

    warnings: list[str] = []

    # 3. Facts must survive.  When the input still contains correction cues the
    #    model is *expected* to delete the superseded values, so drops are
    #    tolerated there.
    tolerant = allow_removals or bool(_CORRECTION_CUE.search(source))
    source_digits = _digit_runs(source)
    output_digits = _digit_runs(text)
    dropped_digits = _multiset_difference(source_digits, output_digits)
    if dropped_digits:
        if not tolerant:
            return ValidationResult(
                False, "dropped_numbers", "Numbers went missing: " + ", ".join(dropped_digits[:4])
            )
        if len(dropped_digits) > 2:
            return ValidationResult(
                False, "dropped_numbers", "Too many numbers went missing."
            )
        warnings.append("dropped numbers: " + ", ".join(dropped_digits))

    # A spelled-out number is still a number.
    #
    # "I have to buy five things, apple, water bottle, ..." contains no digits
    # at all, so the comparison above had nothing to compare and passed. A
    # rewrite that returned only the bullet list - deleting the announcement
    # and the count with it - was therefore accepted. A quantity the speaker
    # said has to survive as the word or as the digit.
    dropped_words = [
        word
        for word in _spoken_number_words(source)
        if word not in text.lower() and _NUMBER_WORDS[word] not in output_digits
    ]
    if dropped_words:
        if not tolerant:
            return ValidationResult(
                False,
                "dropped_numbers",
                "Quantities went missing: " + ", ".join(dict.fromkeys(dropped_words))[:80],
            )
        warnings.append("dropped quantities: " + ", ".join(dict.fromkeys(dropped_words)))
    invented = _multiset_difference(output_digits, source_digits)
    if invented:
        # "five minutes" -> "5 minutes" is formatting, not invention, and so is
        # the "1." "2." of a list the formatter produced.
        spoken = _spoken_numbers(source)
        invented = [
            item
            for item in invented
            if item not in spoken and not _looks_like_list_numbering(text, [item])
        ]
    if invented:
        return ValidationResult(
            False,
            "invented_numbers",
            "Numbers appeared that were not spoken: " + ", ".join(invented[:4]),
        )

    # 4. URLs, emails and paths must be byte-identical.
    for token in set(_URLISH.findall(source)):
        if token not in text:
            return ValidationResult(False, "dropped_identifier", f"'{token}' was altered.")

    # 5. Acronyms and user vocabulary.
    acronyms = [a for a in set(_ACRONYM.findall(source)) if len(a) >= 2 and a not in ("I",)]
    missing_acronyms = _missing(acronyms, text)
    if missing_acronyms and not tolerant:
        return ValidationResult(
            False, "dropped_acronym", "Missing: " + ", ".join(missing_acronyms[:4])
        )

    present_terms = [t for t in (protected_terms or []) if t.lower() in source.lower()]
    missing_terms = _missing(present_terms, text)
    if missing_terms and not tolerant:
        return ValidationResult(
            False, "dropped_vocabulary", "Missing: " + ", ".join(missing_terms[:4])
        )

    # 6. No new content. A style change may reword, but it may never introduce
    #    a fact the speaker did not say. This is the rule that catches a model
    #    "helpfully" adding "Arrived a bit early, but ..." to a short message.
    invented_words = added_content_words(source, text)
    if invented_words:
        return ValidationResult(
            False,
            "invented_content",
            "Words appeared that were not spoken: " + ", ".join(invented_words[:4]),
        )

    # 7. A dictated question stays a question.
    if source.rstrip().endswith("?") and not text.rstrip().endswith(("?", '?"', "?'")):
        if "?" not in text:
            return ValidationResult(False, "lost_question", "A question became a statement.")

    return ValidationResult(True, warnings=warnings)


# Words a rewrite may legitimately introduce: grammar scaffolding, politeness
# and discourse connectives. None of them assert a fact.
_FREE_WORDS = frozenset(
    """
    a an the this that these those it its they them their there here we us our you your
    i me my he him his she her
    is are was were be been being am do does did done doing have has had having
    will would shall should can could may might must
    to of in on at by for with from into onto over under about as than then
    and or but if when while because so that which who whom whose what where why how
    not no nor yes also too very just only even still yet already
    please kindly could would thanks thank hello hi dear regards sincerely best
    however therefore moreover furthermore additionally
    up down out off back again more most less least other another such same
    """.split()
)
_SUFFIXES = ("ing", "edly", "ment", "ness", "tion", "sion", "ies", "ed", "es", "ly", "er", "est", "s")


def _stem(word: str) -> str:
    lowered = word.lower()
    for suffix in _SUFFIXES:
        if len(lowered) > len(suffix) + 2 and lowered.endswith(suffix):
            stem = lowered[: -len(suffix)]
            # English doubles the final consonant before -ing/-ed:
            # "shipping" -> "shipp" -> "ship".
            if len(stem) > 2 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
                stem = stem[:-1]
            return stem
    return lowered


def added_content_words(source: str, produced: str, min_length: int = 4) -> list[str]:
    """Content words present in the rewrite but absent from the input.

    Morphological variants ("ship" -> "shipping") and the function-word
    allowlist above are not counted, so ordinary grammar repair passes while
    genuinely new material is caught.
    """
    source_words = re.findall(r"[^\W\d_]+", source.lower())
    source_set = set(source_words)
    source_stems = {_stem(word) for word in source_words}

    joined = _joinable_identifiers(source_words)

    invented: list[str] = []
    # Split on non-letters so `shipment_service` and `getCustomer` are compared
    # as whole identifiers, not as fragments.
    for raw in re.findall(r"[^\W\d_]+(?:[_-][^\W\d_]+)*", produced):
        lowered = raw.lower()
        bare = re.sub(r"[_-]", "", lowered)
        if len(lowered) < min_length or lowered in _FREE_WORDS or lowered in source_set:
            continue
        if _stem(lowered) in source_stems:
            continue
        # "get customer" -> "getCustomer" is a casing change, not new content.
        if bare in joined:
            continue
        if any(_similar(lowered, candidate) for candidate in source_words):
            continue
        if lowered not in invented:
            invented.append(raw)
    return invented


def _joinable_identifiers(words: list[str], max_span: int = 4) -> set[str]:
    """Concatenations of consecutive source words.

    Developer dictation says identifiers as separate words, so the correct
    output contains a token that never appeared in the input. Allowing only
    *consecutive* runs keeps that from becoming a licence to invent: it can
    produce "getCustomer" from "get customer", but not from "customer get".
    """
    out: set[str] = set()
    for start in range(len(words)):
        for span in range(2, max_span + 1):
            if start + span > len(words):
                break
            out.add("".join(words[start : start + span]))
    return out


def _similar(a: str, b: str) -> bool:
    """True when two words differ only by inflection or a small spelling change."""
    if abs(len(a) - len(b)) > 4:
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if longer.startswith(shorter) and len(longer) - len(shorter) <= 4:
        return True
    try:
        from rapidfuzz import fuzz

        return fuzz.ratio(a, b) >= 88
    except Exception:
        from difflib import SequenceMatcher

        return SequenceMatcher(None, a, b).ratio() >= 0.88


def _multiset_difference(a: list[str], b: list[str]) -> list[str]:
    from collections import Counter

    remaining = Counter(b)
    out: list[str] = []
    for item in a:
        if remaining[item]:
            remaining[item] -= 1
        else:
            out.append(item)
    return out


def _looks_like_list_numbering(text: str, invented: list[str]) -> bool:
    """'1.' '2.' '3.' added by list formatting are not invented facts."""
    markers = set(re.findall(r"(?m)^\s*(\d+)[.)]\s", text))
    return all(item in markers for item in invented)


def sanity_check_final(text: str, max_chars: int = 20000) -> tuple[bool, str]:
    """Last gate before the text reaches another application."""
    if not text or not text.strip():
        return False, "Nothing to insert."
    if len(text) > max_chars:
        return False, f"Result is unusually long ({len(text)} characters) and was not inserted."
    if "\x00" in text:
        return False, "Result contained invalid characters."
    # A long run of one repeated token is the classic local-model failure mode.
    if re.search(r"(\b\w+\b)(?:\s+\1){7,}", text):
        return False, "The model produced a repeated loop."
    return True, ""
