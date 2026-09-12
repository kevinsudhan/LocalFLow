"""Stage 4 - filler removal.

The rule that matters is the one from the spec: *do not blindly delete words*.
"Actually" is a filler in "so, actually, I think" and load-bearing in "the
actual figure was higher".  So the deterministic pass only removes fillers it
can prove are fillers from local syntax, and anything ambiguous is left for the
LLM stage (which sees the whole sentence) or simply kept.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Pure hesitation sounds.  These are never meaningful.
HESITATIONS = {
    "uh", "uhh", "uhhh", "um", "umm", "ummm", "er", "err", "erm", "ah", "ahh",
    "eh", "hmm", "hm", "mm", "mmm", "mhm", "uhm", "ehm",
}

# Discourse markers, only removable at a clause boundary.
CLAUSE_INITIAL = {
    "basically", "honestly", "frankly", "literally", "obviously", "essentially",
    "seriously", "clearly",
}
CLAUSE_INITIAL_AGGRESSIVE = {"so", "well", "now", "look", "right", "okay", "ok"}

MULTIWORD = [
    ("you", "know"),
    ("i", "mean"),
    ("kind", "of"),
    ("sort", "of"),
    ("or", "something"),
    ("or", "whatever"),
    ("if", "that", "makes", "sense"),
    ("you", "know", "what", "i", "mean"),
]

# "kind of" / "sort of" is a real noun phrase after these.
_DET_BEFORE_KINDOF = {
    "a", "an", "the", "this", "that", "these", "those", "what", "some", "any",
    "another", "each", "every", "one", "other",
}
# "you know" is meaningful when it introduces a complement clause.
_COMPLEMENTISERS = {"that", "what", "how", "if", "who", "why", "where", "when", "whether"}

AGGRESSIVENESS = ("conservative", "balanced", "aggressive")


@dataclass
class FillerResult:
    text: str
    removed: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.removed)


_WORD_RE = re.compile(r"\w+(?:['’]\w+)*|\S", re.UNICODE)


def _split(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def _split_spans(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in _WORD_RE.finditer(text)]


def _rebuild(text: str, drop: set[int], spans: list[tuple[str, int, int]]) -> str:
    """Rebuild by cutting dropped spans out of the *original* string.

    Deleting spans rather than re-joining tokens is what keeps everything we did
    not touch byte-identical - spacing, unusual punctuation, and anything the
    tokeniser would have split differently on the way back.
    """
    if not drop:
        return text
    keep: list[str] = []
    cursor = 0
    for index, (_token, start, end) in enumerate(spans):
        if index not in drop:
            continue
        keep.append(text[cursor:start])
        cursor = end
    keep.append(text[cursor:])
    out = "".join(keep)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\s+([,.;:!?…])", r"\1", out)
    out = re.sub(r"([,;:])\s*([,.;:!?…])", r"\2", out)
    out = re.sub(r"(?m)^[ \t]*[,;:]+[ \t]*", "", out)
    out = re.sub(r"\(\s*\)", "", out)
    return out.strip()


def _is_boundary(token: str) -> bool:
    return token in {",", ".", "!", "?", ";", ":", "…", "—", "–", "-", "\n"}


def _rejoin(tokens: list[str]) -> str:
    out: list[str] = []
    for tok in tokens:
        if not out:
            out.append(tok)
        elif tok in {",", ".", "!", "?", ";", ":", "…", "%", ")", "]", "}", "’", "”"}:
            out.append(tok)
        elif tok.startswith("'") and len(tok) <= 3:
            out.append(tok)
        elif out[-1] in {"(", "[", "{", "“", "‘"}:
            out.append(tok)
        else:
            out.append(" " + tok)
    text = "".join(out)
    text = re.sub(r"\s+([,.;:!?…])", r"\1", text)
    text = re.sub(r"([,;:])\s*([,.;:!?…])", r"\2", text)
    text = re.sub(r"^\s*[,;:]\s*", "", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def remove_fillers(
    text: str,
    enabled: bool = True,
    aggressiveness: str = "balanced",
    protected: set[str] | None = None,
) -> FillerResult:
    if not enabled or not text.strip():
        return FillerResult(text)
    level = aggressiveness if aggressiveness in AGGRESSIVENESS else "balanced"
    protected_lower = {p.lower() for p in (protected or set())}

    spans = _split_spans(text)
    tokens = [span[0] for span in spans]
    drop: set[int] = set()
    removed: list[str] = []
    ambiguous: list[str] = []

    # A token is clause-initial if only punctuation/nothing precedes it.
    def clause_initial(idx: int) -> bool:
        j = idx - 1
        while j >= 0 and tokens[j] in {'"', "“", "‘", "(", "'"}:
            j -= 1
        return j < 0 or _is_boundary(tokens[j])

    def next_word(idx: int) -> str:
        j = idx + 1
        while j < len(tokens) and not re.match(r"[^\W\d_]", tokens[j]):
            j += 1
        return tokens[j].lower() if j < len(tokens) else ""

    def prev_word(idx: int) -> str:
        j = idx - 1
        while j >= 0 and not re.match(r"[^\W\d_]", tokens[j]):
            j -= 1
        return tokens[j].lower() if j >= 0 else ""

    def last_kept_is_boundary(index: int) -> bool:
        j = index - 1
        while j >= 0 and j in drop:
            j -= 1
        return j < 0 or _is_boundary(tokens[j])

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        low = tok.lower().strip(".")

        if low in protected_lower:
            i += 1
            continue

        # 1. Hesitations: always removable.
        if low in HESITATIONS:
            drop.add(i)
            removed.append(tok)
            i += 1
            # Swallow a comma that only existed to fence the hesitation.
            if i < len(tokens) and tokens[i] == "," and last_kept_is_boundary(i):
                drop.add(i)
                i += 1
            continue

        # 2. Multi-word markers.
        matched = _match_multiword(tokens, i)
        if matched:
            phrase = tuple(t.lower() for t in tokens[i : i + matched])
            decision = _judge_multiword(phrase, prev_word(i), next_word(i + matched - 1), level)
            if decision == "remove":
                removed.append(" ".join(tokens[i : i + matched]))
                drop.update(range(i, i + matched))
                i += matched
                if i < len(tokens) and tokens[i] == "," and last_kept_is_boundary(i):
                    drop.add(i)
                    i += 1
                continue
            if decision == "ambiguous":
                ambiguous.append(" ".join(tokens[i : i + matched]))
            i += matched
            continue

        # 3. Single-word discourse markers at a clause boundary.
        if low in CLAUSE_INITIAL or (level == "aggressive" and low in CLAUSE_INITIAL_AGGRESSIVE):
            if level == "conservative":
                i += 1
                continue
            followed_by_comma = i + 1 < len(tokens) and tokens[i + 1] == ","
            if clause_initial(i) and (followed_by_comma or level == "aggressive"):
                removed.append(tok)
                drop.add(i)
                i += 1
                if i < len(tokens) and tokens[i] == ",":
                    drop.add(i)
                    i += 1
                continue
            ambiguous.append(tok)
            i += 1
            continue

        # 4. Discourse "like": only when fenced by commas, which is the one
        #    position where it cannot be a verb, preposition or conjunction.
        if low == "like" and level != "conservative":
            fenced = (
                i > 0
                and tokens[i - 1] == ","
                and i + 1 < len(tokens)
                and tokens[i + 1] == ","
            )
            if fenced:
                removed.append(tok)
                drop.update({i - 1, i})   # the opening comma goes too
                i += 2                    # and skip the closing comma
                continue
            if prev_word(i) in {"was", "were", "is", "are", "just", "all"}:
                ambiguous.append(tok)
            i += 1
            continue

        i += 1

    return FillerResult(_rebuild(text, drop, spans), removed, ambiguous)


def _match_multiword(tokens: list[str], i: int) -> int:
    best = 0
    for phrase in MULTIWORD:
        n = len(phrase)
        if i + n > len(tokens):
            continue
        if [t.lower() for t in tokens[i : i + n]] == list(phrase) and n > best:
            best = n
    return best


def _judge_multiword(phrase: tuple[str, ...], prev: str, nxt: str, level: str) -> str:
    """"remove" | "keep" | "ambiguous" for a matched multi-word marker."""
    if phrase in (("kind", "of"), ("sort", "of")):
        if prev in _DET_BEFORE_KINDOF:
            return "keep"                      # "a kind of bird"
        if nxt in {"like", "thing", "things"}:
            return "keep"
        return "remove" if level != "conservative" else "ambiguous"

    if phrase == ("you", "know"):
        if nxt in _COMPLEMENTISERS:
            return "keep"                      # "you know that it shipped"
        if prev in {"do", "don't", "did", "didn't", "does", "let", "should", "would", "i"}:
            return "keep"                      # "do you know", "let you know"
        return "remove" if level != "conservative" else "ambiguous"

    if phrase == ("i", "mean"):
        # Handled earlier as a correction cue; here it is only a filler when
        # nothing was corrected, and only at a clause boundary.
        return "ambiguous" if level == "conservative" else "remove"

    if phrase in (("or", "something"), ("or", "whatever"), ("if", "that", "makes", "sense")):
        return "remove" if level == "aggressive" else "ambiguous"

    if phrase == ("you", "know", "what", "i", "mean"):
        return "remove" if level != "conservative" else "ambiguous"

    return "keep"


def filler_density(text: str) -> float:
    """Fraction of tokens that look like fillers - feeds the LLM gate."""
    tokens = [t.lower() for t in _split(text) if re.match(r"[^\W\d_]", t)]
    if not tokens:
        return 0.0
    hits = sum(1 for t in tokens if t in HESITATIONS or t in CLAUSE_INITIAL)
    return hits / len(tokens)
