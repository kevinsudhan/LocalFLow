"""Tokenisation shared by the deterministic processing stages.

Every stage that rewrites a transcript needs to look at words *and* the
punctuation between them, and then put the text back together without mangling
spacing.  Doing that once, here, keeps the rewrite stages honest.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

WORD_RE = re.compile(
    r"\d+(?:[.,:]\d+)+"            # 4:30, 1.5, 1,000
    r"|\w+(?:['’‑-]\w+)*"          # 15th, shipment_service, don't, co-op
    r"|\S",
    re.UNICODE,
)
TERMINAL = ".!?…"
CLOSERS = ")]}\"'”’"
OPENERS = "([{\"'“‘"
NO_SPACE_BEFORE = set(",.;:!?%)]}…”’") | {"'s", "n't"}


@dataclass
class Token:
    text: str
    index: int

    @property
    def lower(self) -> str:
        return self.text.lower()

    @property
    def is_number(self) -> bool:
        return bool(re.fullmatch(r"\d+(?:[.,:]\d+)*", self.text))

    @property
    def is_word(self) -> bool:
        # "15th" and "h2o" are words; "4:30" and "500" are numbers.
        return bool(re.search(r"\w", self.text, re.UNICODE)) and not self.is_number

    @property
    def is_punct(self) -> bool:
        return not self.is_word and not self.is_number

    @property
    def is_terminal(self) -> bool:
        return self.text in (".", "!", "?", "…")

    @property
    def is_boundary(self) -> bool:
        """Punctuation that marks a pause a speaker could correct across."""
        return self.text in (",", ";", ":", "-", "–", "—", "…") or self.is_terminal


def tokenize(text: str) -> list[Token]:
    return [Token(m.group(0), i) for i, m in enumerate(WORD_RE.finditer(text))]


def detokenize(tokens: list[Token] | list[str]) -> str:
    """Rebuild text with conventional English spacing."""
    parts: list[str] = []
    prev = ""
    for tok in tokens:
        text = tok if isinstance(tok, str) else tok.text
        if not text:
            continue
        if not parts:
            parts.append(text)
            prev = text
            continue
        if text in NO_SPACE_BEFORE or text.startswith("'") and len(text) <= 3:
            parts.append(text)
        elif prev in OPENERS:
            parts.append(text)
        elif text == "\n" or prev == "\n":
            parts.append(text)
        elif prev in ("-", "‑") and text not in TERMINAL:
            parts.append(text)
        else:
            parts.append(" " + text)
        prev = text
    out = "".join(parts)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n[ \t]+", "\n", out)
    return out.strip()


def split_sentences(text: str) -> list[str]:
    """Sentence split that tolerates abbreviations and decimals."""
    if not text.strip():
        return []
    protected = re.sub(
        r"\b(Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|e\.g|i\.e|Inc|Ltd|Co|approx|No)\.",
        lambda m: m.group(0).replace(".", "\x00"),
        text,
    )
    protected = re.sub(r"(\d)\.(\d)", "\\1\x00\\2", protected)
    # "1. Finish the CRM" is one list item, not a sentence called "1."
    protected = re.sub(r"(?:(?<=^)|(?<=\s))(\d{1,2})\.(?=\s)", "\\1\x00", protected)
    pieces = re.split(r"(?<=[.!?…])[ \t]+(?=[\"'“‘(\[]?[A-Z0-9])", protected)
    return [p.replace("\x00", ".").strip() for p in pieces if p.strip()]


def sentence_spans(tokens: list[Token]) -> list[tuple[int, int]]:
    """(start, end) token index pairs for each sentence."""
    spans: list[tuple[int, int]] = []
    start = 0
    for i, tok in enumerate(tokens):
        if tok.is_terminal:
            spans.append((start, i + 1))
            start = i + 1
    if start < len(tokens):
        spans.append((start, len(tokens)))
    return spans


def clause_start(tokens: list[Token], index: int) -> int:
    """Walk back to the start of the clause containing ``index``."""
    i = index - 1
    while i >= 0:
        if tokens[i].is_boundary:
            return i + 1
        i -= 1
    return 0


def word_count(text: str) -> int:
    return len([t for t in tokenize(text) if t.is_word or t.is_number])
