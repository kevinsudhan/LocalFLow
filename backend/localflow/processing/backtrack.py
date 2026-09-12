"""Stage 3 - spoken self-correction.

People revise mid-sentence: "I'll send it Monday, actually Tuesday."  The
superseded information has to disappear, and it has to disappear without the
speaker issuing a command.

The safety property that makes this usable is simple: **a correction is only
applied when a concrete replacement target is found.**  If we cannot point at
the span being superseded, the text is left exactly as spoken and the cue is
reported as unresolved so the LLM stage can handle it.  That turns the hard
problem of "is this a correction?" into the much safer one of "can I name what
it replaces?".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .tokens import Token, clause_start, detokenize, tokenize

# Cues that essentially always introduce a revision.
STRONG_CUES: tuple[tuple[str, ...], ...] = (
    ("scratch", "that"),
    ("make", "that"),
    ("let", "s", "say"),
    ("i", "meant"),
    ("i", "mean", "to", "say"),
    ("or", "rather"),
    ("correction",),
    ("no", "wait"),
    ("wait", "no"),
    ("sorry", "i", "mean"),
    ("sorry", "i", "meant"),
    # "rather" alone is not a cue: "I would rather use the other supplier" is
    # ordinary preference, not a revision. Only "or rather" marks one.
)
# Cues that only count as revisions at a pause, and only with a target.
MEDIUM_CUES: tuple[tuple[str, ...], ...] = (
    ("actually",),
    ("no",),
    ("wait",),
    ("sorry",),
    ("i", "mean"),
)
# Only these authorise throwing away a whole preceding sentence.
SENTENCE_CUES: frozenset[tuple[str, ...]] = frozenset(
    {("scratch", "that"), ("correction",), ("let", "me", "rephrase")}
)

WEEKDAYS = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun",
    "today", "tomorrow", "yesterday", "tonight",
}
MONTHS = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "jan", "feb", "mar", "apr",
    "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
}
TIME_WORDS = {"am", "pm", "noon", "midnight", "o'clock", "oclock"}
TIME_PREPS = {"at", "by", "around", "before", "after", "until", "till", "from"}
TIME_RE = re.compile(r"^\d{1,2}[:.]\d{2}$")
CURRENCY_RE = re.compile(r"^[$£€₹]")
# Function words allowed inside a typed chunk without spoiling its type.
CHUNK_GLUE = {
    "the", "a", "an", "of", "next", "last", "this", "in", "on", "around", "about",
    "to", "for", "with", "from", "by", "at",
}

MAX_PASSES = 6
MAX_SPAN_WORDS = 6


@dataclass
class CorrectionEvent:
    kind: str            # "span" | "clause" | "sentence"
    cue: str
    removed: str
    replacement: str
    confidence: float = 0.8


@dataclass
class BacktrackResult:
    text: str
    events: list[CorrectionEvent] = field(default_factory=list)
    unresolved_cues: list[str] = field(default_factory=list)


_STRONG = [tuple(c) for c in STRONG_CUES]
_MEDIUM = [tuple(c) for c in MEDIUM_CUES]


def _match_cue_at(tokens: list[Token], i: int, cues: list[tuple[str, ...]]) -> tuple[int, tuple[str, ...] | None]:
    """Longest cue matching at ``i``: (token length, the cue itself)."""
    best = 0
    found: tuple[str, ...] | None = None
    for cue in cues:
        n = len(cue)
        if i + n > len(tokens):
            continue
        window = [t.lower.strip(".'") for t in tokens[i : i + n]]
        if window == list(cue) and n > best:
            best, found = n, cue
    return best, found


def classify_chunk(words: list[Token]) -> str:
    """The type of a replacement chunk, used to find its twin in earlier text.

    Strict on purpose: every token must either carry the type or be a harmless
    function word.  A loose classifier would let "Friday and tell him" count as
    a weekday and swallow half a sentence.
    """
    if not words:
        return "generic"
    lowers = [w.lower.strip(".") for w in words]

    def consistent(pred) -> bool:
        return all(pred(w, low) or low in CHUNK_GLUE for w, low in zip(words, lowers))

    if any(TIME_RE.match(w.text) for w in words) or any(low in TIME_WORDS for low in lowers):
        if consistent(lambda w, low: TIME_RE.match(w.text) or low in TIME_WORDS or w.is_number):
            return "time"
    if any(low in WEEKDAYS for low in lowers):
        if consistent(lambda w, low: low in WEEKDAYS):
            return "weekday"
    if any(low in MONTHS for low in lowers):
        if consistent(lambda w, low: low in MONTHS or w.is_number):
            return "date"
    if any(CURRENCY_RE.match(w.text) for w in words):
        if consistent(lambda w, low: CURRENCY_RE.match(w.text) or w.is_number):
            return "money"
    if all(w.is_number for w in words):
        return "number"
    if any(w.is_word and w.text[:1].isupper() for w in words):
        if consistent(lambda w, low: w.is_word and w.text[:1].isupper()):
            return "proper"
    return "generic"


def _token_matches(tok: Token, kind: str, prev: Token | None) -> bool:
    low = tok.lower.strip(".")
    if kind == "time":
        if TIME_RE.match(tok.text) or low in TIME_WORDS:
            return True
        # "at 4" has to be replaceable by "4:30".
        return bool(tok.is_number and prev is not None and prev.lower in TIME_PREPS)
    if kind == "weekday":
        return low in WEEKDAYS
    if kind == "date":
        return low in MONTHS or (tok.is_number and len(tok.text) <= 2)
    if kind == "money":
        return bool(CURRENCY_RE.match(tok.text)) or tok.is_number
    if kind == "number":
        return tok.is_number
    if kind == "proper":
        return bool(tok.is_word and tok.text[:1].isupper())
    return False


def token_kind(tok: Token) -> str:
    """The type of a single token, used to align a chunk against earlier text."""
    low = tok.lower.strip(".")
    if TIME_RE.match(tok.text) or low in TIME_WORDS:
        return "time"
    if low in WEEKDAYS:
        return "weekday"
    if low in MONTHS:
        return "date"
    if CURRENCY_RE.match(tok.text):
        return "money"
    if tok.is_number:
        return "number"
    if tok.is_word and tok.text[:1].isupper():
        return "proper"
    return "other"


def _aligns(chunk_tok: Token, cand: Token, prev: Token | None) -> bool:
    """Could ``cand`` be the thing ``chunk_tok`` is replacing?"""
    kind = token_kind(chunk_tok)
    if kind == "other":
        # Glue words must match literally ("to Karthik" replaces "to Rahul").
        return chunk_tok.lower == cand.lower
    if kind == "proper" and cand.index == 0:
        return False  # a sentence-initial capital is not evidence of a name
    cand_kind = token_kind(cand)
    if cand_kind == kind:
        return True
    # "at 4" has to be replaceable by "4:30".
    if kind == "time" and cand.is_number and prev is not None and prev.lower in TIME_PREPS:
        return True
    if kind == "number" and cand_kind in ("time", "money"):
        return True
    if kind == "money" and cand.is_number:
        return True
    return False


def _find_aligned_span(before: list[Token], chunk: list[Token]) -> tuple[int, int] | None:
    """The most recent span in ``before`` that the chunk supersedes."""
    n = len(chunk)
    if not before or n == 0 or n > len(before):
        return None
    if all(token_kind(t) == "other" for t in chunk):
        return None
    for end in range(len(before), n - 1, -1):
        span = before[end - n : end]
        if all(c.lower == b.lower for c, b in zip(chunk, span)):
            continue  # identical text is not a correction
        prev = before[end - n - 1] if end - n - 1 >= 0 else None
        ok = True
        for offset, (c, b) in enumerate(zip(chunk, span)):
            before_tok = span[offset - 1] if offset > 0 else prev
            if not _aligns(c, b, before_tok):
                ok = False
                break
        if ok:
            return end - n, end
    return None


def _strip_edges(tokens: list[Token]) -> list[Token]:
    out = list(tokens)
    while out and out[0].is_punct:
        out.pop(0)
    while out and out[-1].is_punct:
        out.pop()
    return out


def _common_prefix(a: list[str], b: list[str]) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _gap_start(tokens: list[Token], cue_start: int) -> int:
    """Index of the first punctuation token in the pause before the cue.

    A sentence terminator immediately before the cue is part of that pause too:
    "I will send it Monday. Actually Tuesday." must merge into one sentence, not
    leave the old full stop stranded as "Tuesday..".
    """
    j = cue_start
    while j > 0 and tokens[j - 1].is_punct and not tokens[j - 1].is_terminal:
        j -= 1
    while j > 0 and tokens[j - 1].is_terminal:
        j -= 1
    while j > 0 and tokens[j - 1].is_punct and not tokens[j - 1].is_terminal:
        j -= 1
    return j


def _looks_like_revision(tokens: list[Token], cue_start: int, cue_end: int) -> bool:
    """Whether an unanchored cue plausibly introduces a revision.

    Used only to decide whether to *flag* a cue for the LLM, never to rewrite.
    The test is whether comparable material sits on both sides of it, which is
    what separates "buy a record actually a present" from "I don't actually
    know" - the latter has no noun phrase after the cue to swap in.
    """
    after = [t for t in tokens[cue_end:] if t.is_word or t.is_number]
    before = [t for t in tokens[:cue_start] if t.is_word or t.is_number]
    if not after or not before:
        return False
    # A cue followed by a whole clause is ordinary prose ("actually I think we
    # should reconsider the whole plan"); a short chunk is a candidate swap.
    if len(after) > 8:
        return False
    kinds_after = {token_kind(t) for t in after[:4]}
    kinds_before = {token_kind(t) for t in before[-6:]}
    typed = (kinds_after - {"other"}) & (kinds_before - {"other"})
    if typed:
        return True
    # Number words are a type the token classifier cannot see, and they are the
    # single most common thing people revise ("at five, actually six").
    numberish = _NUMBER_WORDS & {t.lower for t in after[:3]}
    if numberish and (_NUMBER_WORDS & {t.lower for t in before[-5:]}):
        return True

    # Determiner symmetry: "buy a record actually a present". Repeating the
    # determiner is a strong signal that the speaker is swapping one noun
    # phrase for another, and it does not fire on "actually know the answer"
    # because that has no determiner in the swap position.
    head = after[0].lower
    if head in _DETERMINERS and head in {t.lower for t in before[-5:]}:
        return True
    return False


_DETERMINERS = {
    "a", "an", "the", "my", "your", "his", "her", "our", "their", "this", "that",
}


_NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty",
    "noon", "midnight", "half", "quarter",
}


def _sentence_start(tokens: list[Token], index: int) -> int:
    i = index - 1
    while i >= 0:
        if tokens[i].is_terminal:
            return i + 1
        i -= 1
    return 0


def _apply_once(text: str) -> tuple[str, CorrectionEvent | None, list[str]]:
    tokens = tokenize(text)
    unresolved: list[str] = []
    i = 0
    while i < len(tokens):
        strong_len, strong_cue = _match_cue_at(tokens, i, _STRONG)
        medium_len, _ = (0, None) if strong_len else _match_cue_at(tokens, i, _MEDIUM)
        cue_len = strong_len or medium_len
        if not cue_len:
            i += 1
            continue

        # Absorb a chain of cues: "wait, make that ..." or "actually no".
        end = i + cue_len
        chained = False
        while end < len(tokens):
            skip = end
            while skip < len(tokens) and tokens[skip].is_punct and not tokens[skip].is_terminal:
                skip += 1
            extra_s, extra_cue = _match_cue_at(tokens, skip, _STRONG)
            extra_m, _ = (0, None) if extra_s else _match_cue_at(tokens, skip, _MEDIUM)
            extra = extra_s or extra_m
            if not extra:
                break
            chained = True
            if extra_s:
                strong_cue = extra_cue
                strong_len = extra_s
            end = skip + extra

        cue_text = detokenize(tokens[i:end])
        gap = _gap_start(tokens, i)
        prev_tok = tokens[gap - 1] if gap > 0 else None
        followed_by_comma = end < len(tokens) and tokens[end].text == ","
        boundary_ok = bool(
            strong_len
            or chained
            or (gap < i)
            or (prev_tok is not None and prev_tok.is_boundary)
            or followed_by_comma
        )
        if i == 0:
            i = end
            continue
        if not boundary_ok:
            # Whisper often does not punctuate a mid-sentence revision, so
            # "buy a record actually a present" arrives with no pause to anchor
            # on. Deterministically rewriting that would be reckless - but
            # staying silent hides it from the LLM gate too, which is how these
            # used to slip through untouched. Report it and move on.
            if _looks_like_revision(tokens, i, end):
                unresolved.append(cue_text)
            i = end
            continue

        after_end = end
        while after_end < len(tokens) and not tokens[after_end].is_terminal:
            after_end += 1
        after = _strip_edges(tokens[end:after_end])
        before = _strip_edges(tokens[:gap])
        if not before:
            i = end
            continue

        result = _try_replace(
            tokens,
            before,
            after,
            cue_start=i,
            cue_end=end,
            gap=gap,
            after_end=after_end,
            sentence_ok=bool(strong_cue in SENTENCE_CUES),
        )
        if result is not None:
            new_tokens, event = result
            event.cue = cue_text
            return detokenize(new_tokens), event, unresolved
        unresolved.append(cue_text)
        i = end
    return text, None, unresolved


def _try_replace(
    tokens: list[Token],
    before: list[Token],
    after: list[Token],
    cue_start: int,
    cue_end: int,
    gap: int,
    after_end: int,
    sentence_ok: bool,
) -> tuple[list[Token], CorrectionEvent] | None:
    after_words = [t for t in after if t.is_word or t.is_number]
    tail = tokens[after_end:]
    if not after_words:
        if sentence_ok:
            # Also drop the cue's own terminal punctuation; there is no
            # replacement clause for it to belong to.
            while tail and tail[0].is_punct:
                tail = tail[1:]
            start = _sentence_start(tokens, _skip_back_punct(tokens, gap))
            removed = detokenize(tokens[start:gap])
            return tokens[:start] + tail, CorrectionEvent("sentence", "", removed, "", 0.75)
        return None

    # (1) Clause level: the speaker restated the whole clause.
    cstart = clause_start(tokens, gap)
    clause = [t for t in tokens[cstart:gap] if t.is_word or t.is_number]
    if len(after_words) >= 3 and len(clause) >= 3:
        prefix = _common_prefix([t.lower for t in clause], [t.lower for t in after_words])
        if prefix >= 2 and prefix / float(min(len(clause), len(after_words))) >= 0.4:
            removed = detokenize(tokens[cstart:gap])
            new_tokens = tokens[:cstart] + after + tokens[after_end:]
            return new_tokens, CorrectionEvent("clause", "", removed, detokenize(after), 0.9)

    # (2) Chunk level: a short typed chunk supersedes an earlier one of the
    #     same type.  Probe longest-first so "5 pm" beats a bare "5".
    upper = min(len(after_words), MAX_SPAN_WORDS)
    for n in range(upper, 0, -1):
        chunk = after_words[:n]
        if classify_chunk(chunk) == "generic":
            continue
        span = _find_aligned_span(before, chunk)
        if span is None:
            continue
        start_idx = before[span[0]].index
        stop_idx = before[span[1] - 1].index + 1
        if stop_idx > gap:
            continue
        chunk_end = chunk[-1].index + 1
        removed = detokenize(before[span[0] : span[1]])
        new_tokens = (
            tokens[:start_idx]
            + tokens[chunk[0].index : chunk_end]
            + tokens[stop_idx:gap]
            + tokens[chunk_end:]
        )
        return new_tokens, CorrectionEvent("span", "", removed, detokenize(chunk), 0.85)

    # (3) Explicit "scratch that": drop the preceding sentence entirely.
    if sentence_ok:
        start = _sentence_start(tokens, _skip_back_punct(tokens, gap))
        if start < gap:
            removed = detokenize(tokens[start:gap])
            return tokens[:start] + after + tail, CorrectionEvent(
                "sentence", "", removed, detokenize(after), 0.7
            )
    return None


def _skip_back_punct(tokens: list[Token], index: int) -> int:
    while index > 0 and tokens[index - 1].is_punct:
        index -= 1
    return index


def resolve_corrections(text: str, enabled: bool = True) -> BacktrackResult:
    """Apply spoken self-corrections until the text stops changing."""
    if not enabled or not text.strip():
        return BacktrackResult(text, [], [])
    events: list[CorrectionEvent] = []
    unresolved: list[str] = []
    # A run of dots is one pause, not three sentence ends.
    current = re.sub(r"\.{2,}", "…", text)
    for _ in range(MAX_PASSES):
        nxt, event, pending = _apply_once(current)
        unresolved = pending
        if event is None:
            break
        events.append(event)
        if nxt.strip() == current.strip():
            break
        current = nxt
    if events:
        # Merging across a sentence boundary can leave doubled terminators.
        current = re.sub(r"([.!?])[ \t]*([.!?])+", r"\1", current)
        current = re.sub(r"\s+([,.;:!?])", r"\1", current)
    return BacktrackResult(current, events, unresolved)


def has_unresolved_cues(text: str) -> bool:
    return bool(resolve_corrections(text).unresolved_cues)
