"""Stage 2 - personal vocabulary and learned corrections.

Whisper will never have heard of "Araxys" or "ICEGATE".  Two mechanisms fix
that: the terms are fed to Whisper as an ``initial_prompt`` so it is *biased*
toward them, and whatever still comes out wrong is repaired here by matching
the mishearing back to the canonical spelling.

Matching is phonetic-ish rather than literal: ASR errors are sound-alike errors
("Araxis" for "Araxys"), so a normalised edit-distance over a consonant
skeleton catches far more than exact string replacement, while the similarity
floor keeps genuinely different words apart.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

try:
    from rapidfuzz import fuzz

    _HAVE_RAPIDFUZZ = True
except Exception:  # pragma: no cover - rapidfuzz is a hard dependency in prod
    from difflib import SequenceMatcher

    _HAVE_RAPIDFUZZ = False

_WORD_RE = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", re.UNICODE)
_VOWELS = str.maketrans("", "", "aeiou")


@dataclass
class VocabularyEntry:
    term: str
    sounds_like: list[str] = field(default_factory=list)
    case_sensitive: bool = True
    category: str = "general"


@dataclass
class VocabularyResult:
    text: str
    applied: list[tuple[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.applied)


def _similarity(a: str, b: str) -> float:
    if _HAVE_RAPIDFUZZ:
        return float(fuzz.ratio(a, b))
    return SequenceMatcher(None, a, b).ratio() * 100.0


def _skeleton(word: str) -> str:
    """Consonant skeleton - collapses the vowel confusions ASR makes most."""
    lowered = word.lower()
    collapsed = re.sub(r"(.)\1+", r"\1", lowered)
    stripped = collapsed.translate(_VOWELS)
    return stripped or collapsed


class VocabularyEngine:
    """Applies user vocabulary and promoted learned corrections to a transcript."""

    def __init__(self, threshold: int = 86):
        self.threshold = threshold
        self._entries: list[VocabularyEntry] = []
        self._exact: dict[str, str] = {}
        self._learned: dict[str, str] = {}
        self._learned_phrases: list[tuple[str, str]] = []
        self._by_length: dict[int, list[VocabularyEntry]] = {}

    # -- loading -----------------------------------------------------------
    def load(self, entries: Iterable[dict], corrections: Iterable[dict] | None = None) -> None:
        self._entries = []
        self._exact = {}
        self._by_length = {}
        for raw in entries:
            term = (raw.get("term") or "").strip()
            if not term:
                continue
            entry = VocabularyEntry(
                term=term,
                sounds_like=[s for s in (raw.get("sounds_like") or []) if s],
                case_sensitive=bool(raw.get("case_sensitive", True)),
                category=raw.get("category", "general"),
            )
            self._entries.append(entry)
            self._exact[term.lower()] = term
            for alias in entry.sounds_like:
                self._exact[alias.lower().strip()] = term
            self._by_length.setdefault(len(term.split()), []).append(entry)

        self._learned = {}
        self._learned_phrases = []
        for raw in corrections or []:
            wrong = (raw.get("wrong") or "").strip()
            correct = (raw.get("correct") or "").strip()
            if not wrong or not correct:
                continue
            if " " in wrong:
                self._learned_phrases.append((wrong, correct))
            else:
                self._learned[wrong.lower()] = correct
        self._learned_phrases.sort(key=lambda kv: -len(kv[0]))

    @property
    def terms(self) -> list[str]:
        return [e.term for e in self._entries]

    def prompt_terms(self, limit: int = 48) -> list[str]:
        """Terms worth spending Whisper's prompt budget on (longest first)."""
        ordered = sorted(self._entries, key=lambda e: (-len(e.term), e.term))
        return [e.term for e in ordered[:limit]]

    @property
    def size(self) -> int:
        return len(self._entries)

    # -- application -------------------------------------------------------
    def apply(self, text: str, enabled: bool = True) -> VocabularyResult:
        if not enabled or not text or (not self._entries and not self._learned
                                       and not self._learned_phrases):
            return VocabularyResult(text)
        applied: list[tuple[str, str]] = []
        result = text

        # 1. Learned multi-word corrections, longest first.
        for wrong, correct in self._learned_phrases:
            pattern = re.compile(r"(?<!\w)" + re.escape(wrong) + r"(?!\w)", re.IGNORECASE)
            if pattern.search(result):
                result = pattern.sub(correct, result)
                applied.append((wrong, correct))

        # 2. Any variant containing a space, longest first. Token-level repair
        #    below cannot see across a space, so "ice gate" -> "ICEGATE" has to
        #    happen here even though the canonical term is a single word.
        multiword: list[tuple[str, str]] = []
        for entry in self._entries:
            for variant in [entry.term] + entry.sounds_like:
                if " " not in variant:
                    continue
                if variant == entry.term:
                    continue
                multiword.append((variant, entry.term))
            if " " in entry.term:
                multiword.append((entry.term, entry.term))
        multiword.sort(key=lambda pair: -len(pair[0]))

        for variant, canonical in multiword:
            pattern = re.compile(r"(?<!\w)" + re.escape(variant) + r"(?!\w)", re.IGNORECASE)
            if pattern.search(result):
                replaced = pattern.sub(canonical, result)
                if replaced != result:
                    result = replaced
                    applied.append((variant, canonical))

        # 3. Token-level repair.
        def repair(match: re.Match) -> str:
            word = match.group(0)
            replacement = self._repair_word(word)
            if replacement is not None and replacement != word:
                applied.append((word, replacement))
                return replacement
            return word

        result = _WORD_RE.sub(repair, result)
        return VocabularyResult(result, applied)

    def _repair_word(self, word: str) -> str | None:
        lowered = word.lower()
        learned = self._learned.get(lowered)
        if learned:
            return learned
        exact = self._exact.get(lowered)
        if exact:
            return exact if exact != word else None

        if len(word) < 4:
            return None
        skeleton = _skeleton(word)
        best: tuple[float, str] | None = None
        for entry in self._entries:
            if " " in entry.term:
                continue
            candidates = [entry.term] + entry.sounds_like
            for candidate in candidates:
                if abs(len(candidate) - len(word)) > max(3, len(candidate) // 2):
                    continue
                score = _similarity(lowered, candidate.lower())
                if score < self.threshold:
                    # A matching consonant skeleton rescues vowel-only errors.
                    if _skeleton(candidate) == skeleton and score >= self.threshold - 12:
                        score = float(self.threshold)
                    else:
                        continue
                if best is None or score > best[0]:
                    best = (score, entry.term)
        if best is None:
            return None
        return best[1]

    # -- learning ----------------------------------------------------------
    @staticmethod
    def diff_words(before: str, after: str) -> list[tuple[str, str]]:
        """Word-level substitutions between the produced text and the user's edit.

        Only 1:1 substitutions are reported.  Insertions and deletions are not
        vocabulary evidence, and treating them as such poisons the dictionary.
        """
        import difflib

        a = _WORD_RE.findall(before)
        b = _WORD_RE.findall(after)
        pairs: list[tuple[str, str]] = []
        matcher = difflib.SequenceMatcher(None, [w.lower() for w in a], [w.lower() for w in b])
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "replace" or (i2 - i1) != (j2 - j1):
                continue
            for offset in range(i2 - i1):
                wrong, correct = a[i1 + offset], b[j1 + offset]
                if wrong.lower() == correct.lower():
                    continue
                if len(wrong) < 3 or len(correct) < 3:
                    continue
                if _similarity(wrong.lower(), correct.lower()) < 55:
                    continue  # a different word, not a spelling fix
                pairs.append((wrong, correct))
        return pairs
