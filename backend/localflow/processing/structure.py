"""Stage 7 - paragraphs and lists.

Two separate signals drive structure:

* **Paragraphs** come from the *audio*.  A long pause between speech segments
  at a sentence boundary is a paragraph break, and we have those timings from
  VAD and from Whisper's segments.  Guessing paragraph breaks from text alone
  is unreliable; guessing them from silence is not.
* **Lists** come from an explicit ascending enumeration.  "One ... two ...
  three ..." is a list.  "I need two apples and three oranges" is not, and the
  ascending-run-starting-at-one requirement is what separates them.
"""
from __future__ import annotations

import re

from .tokens import split_sentences

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}
BULLET_WORDS = {"bullet", "dash", "point"}

_MARKER_RE = re.compile(
    r"^\s*(?:(?P<digit>\d{1,2})\s*[.)]?|(?P<word>[a-z]+))\s*[,.)\-:]?\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
MIN_ITEMS = 2
MIN_ITEM_WORDS = 2


def _marker_value(sentence: str) -> tuple[int, str] | None:
    """(index, remaining text) when a sentence opens with a list marker."""
    match = _MARKER_RE.match(sentence)
    if not match:
        return None
    rest = match.group("rest").strip()
    if len(rest.split()) < MIN_ITEM_WORDS:
        return None
    if match.group("digit"):
        return int(match.group("digit")), rest
    word = (match.group("word") or "").lower()
    if word in NUMBER_WORDS:
        return NUMBER_WORDS[word], rest
    if word in ORDINAL_WORDS:
        return ORDINAL_WORDS[word], rest
    return None


def _clean_item(text: str) -> str:
    text = text.strip().rstrip(".").strip()
    text = re.sub(r"^and\s+", "", text, flags=re.IGNORECASE)
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text


def detect_list(text: str, enabled: bool = True) -> str:
    """Format an explicit spoken enumeration as a numbered list."""
    if not enabled or not text.strip():
        return text
    for detector in (
        _list_from_sentences,
        _list_from_inline,
        _bullets_from_inline,
        _list_from_series,
    ):
        converted = detector(text)
        if converted is not None:
            return converted
    return text


# What makes a comma series a list rather than prose.
#
# Commas are the most common punctuation in English, so they are far too weak a
# signal on their own: "I went to the shop, bought milk and came home" must stay
# a sentence. The evidence required is an explicit announcement - a list noun
# ("five things", "the items", "the following") or a stated count - somewhere in
# the sentence that introduces the series.
_LIST_NOUNS = re.compile(
    r"(?<![\w])(?:lists?|items?|things?|following|steps?|ingredients?|agenda|"
    r"errands?|groceries|to-?dos?|points?)(?![\w])",
    re.IGNORECASE,
)
# A number followed by a plural noun announces a list, whatever the noun:
# "five things", "five AIs", "three colours". Restricting this to a fixed
# vocabulary meant "the five AIs that I know are ..." was invisible, which is
# exactly the sort of sentence people dictate.
_STATED_COUNT = re.compile(
    r"(?<![\w])(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"\s+([A-Za-z][\w'-]{2,})(?![\w])",
    re.IGNORECASE,
)
# Counted nouns that are measurements rather than enumerations. "I waited five
# minutes" announces nothing.
_COUNT_STOPWORDS = frozenset(
    {
        "minutes", "seconds", "hours", "days", "weeks", "months", "years",
        "times", "percent", "dollars", "rupees", "pounds", "euros", "cents",
        "miles", "metres", "meters", "kilometres", "kilometers", "degrees",
        "grams", "kilos", "kilograms", "litres", "liters", "copies", "pages",
    }
)
# "It will be X, Y and Z" - a throat-clearing opener before the series proper.
# Dropped from the output, because it reads as noise once the items are bullets.
_SERIES_OPENER = re.compile(
    r"^(?:it(?:\u2019s|'s|\u2019ll be|'ll be| will be| is| would be)"
    r"|they(?:\u2019re|'re|\u2019ll be|'ll be| are| will be)"
    r"|these are|those are|that(?:\u2019s|'s| is)|namely|specifically"
    r"|the list is|my list is|here(?:\u2019s|'s| is|\u2019s a| are))\s+",
    re.IGNORECASE,
)
# "five things which are A, B and C" - the announcement and the series in one
# breath, with no colon anywhere. Whisper punctuates this as a single sentence,
# so without these connectors the series is invisible to the detector.
#
# Only the copula is consumed, never the words in front of it: "the five AIs
# that I know are X, Y, Z" must keep "that I know", or the heading quietly
# changes what the speaker said. A relative pronoun left dangling by the split
# is trimmed separately, below.
_SERIES_CONNECTOR = re.compile(
    r",?\s+(?:and\s+they\s+are|and\s+those\s+are|and\s+these\s+are"
    r"|namely|as\s+follows|are|is|were|was)\s*[:,]?\s+",
    re.IGNORECASE,
)
# Left behind when the copula is split off "... which are X, Y and Z".
_DANGLING_PRONOUN = re.compile(r"[,\s]+(?:which|that|who)$", re.IGNORECASE)

MIN_SERIES_ITEMS = 3
MAX_SERIES_ITEM_WORDS = 6


def _stated_count(text: str) -> int | None:
    """The number of items the speaker said there would be, if they said one."""
    for match in _STATED_COUNT.finditer(text):
        if match.group(2).lower() in _COUNT_STOPWORDS:
            continue
        token = match.group(1).lower()
        return int(token) if token.isdigit() else NUMBER_WORDS.get(token)
    return None


def _announces_a_list(text: str) -> bool:
    return bool(_LIST_NOUNS.search(text)) or _stated_count(text) is not None


def _series_items(body: str) -> list[str] | None:
    """Split 'a, b, c and d' into items, or decide it is not a series."""
    body = re.sub(r"[.!?]+$", "", body.strip()).strip()
    parts = [part.strip() for part in body.split(",") if part.strip()]
    if not parts:
        return None
    # A closed enumeration ends on a conjunction. With an Oxford comma that
    # conjunction is its own part ("..., and butter"); without one it hides
    # inside the final part ("... bread and butter"), which then has to be
    # split in two. With no conjunction at all this is a sentence that trailed
    # off, and bulleting it would invent structure nobody dictated.
    if not re.match(r"^(?:and|or)\s+\S", parts[-1], re.IGNORECASE):
        halves = re.split(r"\s+(?:and|or)\s+", parts[-1], 1, flags=re.IGNORECASE)
        if len(halves) != 2 or not all(half.strip() for half in halves):
            return None
        parts[-1:] = [halves[0].strip(), halves[1].strip()]
    if len(parts) < MIN_SERIES_ITEMS:
        return None
    items = [_clean_item(part) for part in parts]
    if any(not item or len(item.split()) > MAX_SERIES_ITEM_WORDS for item in items):
        return None
    return items


def _list_from_series(text: str) -> str | None:
    """Handle 'I need to buy five things: banana, apple, orange and crayons'.

    By far the commonest way anybody dictates a shopping list, and the one form
    the detectors above cannot see: there is no ascending "one ... two ..." to
    validate against, only commas. It arrives in two shapes, because whether a
    colon appears at all depends on punctuation the speaker never said:

        I need five things: apple, banana and mango.
        I need five things. It will be apple, banana and mango.

    Both are the same utterance, so both have to be recognised.
    """
    if "\n" in text:
        return None
    sentences = [s.strip() for s in split_sentences(text) if s.strip()]
    if not sentences:
        return None

    # Whisper stutters on the last word: "... chilli and guava. Guava."
    # That fragment is not a sentence, but it does sit where the series
    # should be, so the check below would give up before finding the list.
    while len(sentences) >= 2:
        fragment = re.findall(r"[\w']+", sentences[-1].lower())
        if not fragment or len(fragment) > 2:
            break
        if not all(word in sentences[-2].lower() for word in fragment):
            break
        sentences.pop()
    # The series must close the utterance; one buried mid-paragraph is far more
    # likely to be prose than an enumeration.
    last = sentences[-1]
    head, separator, tail = last.partition(":")
    require_count = False

    if separator and _announces_a_list(head):
        items = _series_items(tail)
        opener, lead_sentences = head, sentences[:-1]
    elif len(sentences) >= 2 and _announces_a_list(sentences[-2]):
        # The announcement was its own sentence: "...five things. It will be X,
        # Y and Z." Strip the throat-clearing opener; the colon replaces it.
        items = _series_items(_SERIES_OPENER.sub("", last, count=1))
        opener, lead_sentences = sentences[-2], sentences[:-2]
    elif _announces_a_list(last) and _SERIES_CONNECTOR.search(last):
        # One sentence, no colon: "five things which are A, B and C".
        announcement, series = _SERIES_CONNECTOR.split(last, maxsplit=1)[:2]
        announcement = _DANGLING_PRONOUN.sub("", announcement)
        items = _series_items(series)
        opener, lead_sentences = announcement, sentences[:-1]
    elif _LIST_NOUNS.search(last.partition(",")[0]):
        # "five things, carrot, onion and chilli" - Whisper wrote a comma where
        # the speaker paused, so a comma is all that separates the announcement
        # from the series. That is the weakest separator there is, so this
        # branch demands the strongest evidence: a generic list noun ("things",
        # "items") rather than any counted plural, and a stated count that
        # matches the items exactly. Without both, "I bought 3 books, a pen, a
        # ruler and a bag" would be rewritten as a list of the wrong things.
        announcement, _, series = last.partition(",")
        items = _series_items(series)
        opener, lead_sentences = announcement, sentences[:-1]
        require_count = True
    else:
        return None

    if not items:
        return None

    # If the speaker said how many there would be, believe them: a mismatch
    # means the commas are doing something other than separating items.
    stated = _stated_count(opener)
    if require_count and stated is None:
        return None
    if stated is not None and stated != len(items):
        return None

    lead = " ".join(lead_sentences).strip()
    opener = re.sub(r"[.!?]+$", "", opener).strip()
    prefix = (lead + " " if lead else "") + opener + ":"
    return prefix + "\n\n" + "\n".join("- " + item for item in items)


def _bullets_from_inline(text: str) -> str | None:
    """Handle 'bullet call the supplier bullet confirm the booking'."""
    if "\n" in text:
        return None
    hits = [(m.start(), m.end()) for m in _BULLET_RE.finditer(text)]
    if len(hits) < MIN_BULLETS:
        return None

    items: list[str] = []
    for index, (_start, end) in enumerate(hits):
        stop = hits[index + 1][0] if index + 1 < len(hits) else len(text)
        chunk = text[end:stop].strip(" ,.;:-")
        if len(chunk.split()) < MIN_ITEM_WORDS:
            return None
        items.append(_clean_item(chunk))

    lead = text[: hits[0][0]].strip(" ,.;:-")
    body = "\n".join("- " + item for item in items)
    if lead:
        return re.sub(r"[.!?]+$", "", lead).strip() + ":\n\n" + body
    return body


def _list_from_sentences(text: str) -> str | None:
    if "\n" in text.strip():
        return None
    sentences = split_sentences(text)
    if len(sentences) < MIN_ITEMS + 0:
        return None

    markers: list[tuple[int, int, str]] = []
    for idx, sentence in enumerate(sentences):
        parsed = _marker_value(sentence)
        if parsed:
            markers.append((idx, parsed[0], parsed[1]))

    run = _longest_ascending_run(markers)
    if run is None:
        return None
    start_pos = run[0][0]
    if len(run) < MIN_ITEMS:
        return None
    # The run must reach the end of the utterance; a list that stops mid-way is
    # more likely to be a coincidence than a real enumeration.
    if run[-1][0] != len(sentences) - 1:
        return None

    lead = " ".join(sentences[:start_pos]).strip()
    items = [_clean_item(item) for _, _, item in run]
    body = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(items))
    if lead:
        lead = re.sub(r"[.!?]+$", "", lead).strip() + ":"
        return lead + "\n\n" + body
    return body


def _longest_ascending_run(markers: list[tuple[int, int, str]]) -> list[tuple[int, int, str]] | None:
    """Consecutive sentences numbered 1, 2, 3 ... with no gaps."""
    best: list[tuple[int, int, str]] = []
    current: list[tuple[int, int, str]] = []
    for entry in markers:
        idx, value, _ = entry
        if not current:
            if value == 1:
                current = [entry]
            continue
        prev_idx, prev_val, _ = current[-1]
        if idx == prev_idx + 1 and value == prev_val + 1:
            current.append(entry)
        else:
            if len(current) > len(best):
                best = current
            current = [entry] if value == 1 else []
    if len(current) > len(best):
        best = current
    return best if len(best) >= MIN_ITEMS else None


# Three ways people enumerate out loud, all of which Whisper may produce for
# the same dictation: cardinals ("one"), ordinals ("first"), and digits it has
# already formatted ("1."). Bullets are handled separately because they have no
# inherent order to validate against.
_INLINE_RE = re.compile(
    r"(?<![\w])(?:"
    r"(?P<word>one|two|three|four|five|six|seven|eight|nine|ten)"
    r"|(?P<ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
    r"|(?P<digit>\d{1,2})[.)]"
    r")(?![\w])",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(
    r"(?<![\w])(?:bullet(?:\s+point)?|next\s+point|dash)(?![\w])", re.IGNORECASE
)
MIN_BULLETS = 2

# A determiner or preposition before an ordinal makes it an adjective
# ("the first item"), never a list marker.
_NOT_A_MARKER_AFTER = {
    "the", "a", "an", "my", "your", "his", "her", "our", "their", "this", "that",
    "in", "at", "on", "of", "for", "to", "came", "finished", "ranked", "placed",
    "every", "each", "his", "its", "no", "any",
}


def _list_from_inline(text: str) -> str | None:
    """Handle 'One finish the CRM two fix Outlook three test ICEGATE'."""
    if "\n" in text:
        return None
    hits: list[tuple[int, int, int]] = []
    for match in _INLINE_RE.finditer(text):
        # "the first item", "a second chance", "in third place" are adjectives,
        # not list markers. A real marker opens a clause.
        preceding = text[: match.start()].rstrip()
        if preceding:
            last_word = re.findall(r"[\w']+$", preceding)
            if last_word and last_word[-1].lower() in _NOT_A_MARKER_AFTER:
                continue
        if match.group("digit"):
            hits.append((match.start(), match.end(), int(match.group("digit"))))
        elif match.group("ordinal"):
            hits.append((match.start(), match.end(), ORDINAL_WORDS[match.group("ordinal").lower()]))
        else:
            hits.append((match.start(), match.end(), NUMBER_WORDS[match.group("word").lower()]))
    if len(hits) < MIN_ITEMS:
        return None
    run: list[tuple[int, int, int]] = []
    for hit in hits:
        if not run:
            if hit[2] == 1:
                run = [hit]
            continue
        if hit[2] == run[-1][2] + 1:
            run.append(hit)
        elif hit[2] == 1:
            run = [hit]
    if len(run) < MIN_ITEMS:
        return None

    items: list[str] = []
    for i, (_start, end, _value) in enumerate(run):
        stop = run[i + 1][0] if i + 1 < len(run) else len(text)
        chunk = text[end:stop].strip(" ,.;:-")
        if len(chunk.split()) < MIN_ITEM_WORDS:
            return None
        items.append(_clean_item(chunk))
    lead = text[: run[0][0]].strip(" ,.;:-")
    body = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(items))
    if lead:
        lead = re.sub(r"[.!?]+$", "", lead).strip() + ":"
        return lead + "\n\n" + body
    return body


def paragraphs_from_segments(
    text: str,
    segments: list[dict],
    gap_seconds: float = 1.1,
    enabled: bool = True,
) -> str:
    """Insert paragraph breaks where the speaker actually paused.

    ``segments`` are Whisper segments with ``start``/``end``/``text``.  A break
    is only inserted when the preceding segment ends a sentence, so a pause for
    breath mid-clause does not split a paragraph.
    """
    if not enabled or not segments or len(segments) < 2 or not text.strip():
        return text
    if "\n\n" in text:
        return text

    breaks: list[str] = []
    for prev, nxt in zip(segments, segments[1:]):
        gap = float(nxt.get("start", 0.0)) - float(prev.get("end", 0.0))
        prev_text = (prev.get("text") or "").strip()
        if gap >= gap_seconds and prev_text.endswith((".", "!", "?", "…")):
            breaks.append(prev_text)

    if not breaks:
        return text
    result = text
    for anchor in breaks:
        tail = anchor[-40:]
        idx = result.find(tail)
        if idx == -1:
            continue
        cut = idx + len(tail)
        if cut >= len(result.rstrip()):
            continue
        result = result[:cut].rstrip() + "\n\n" + result[cut:].lstrip()
    return re.sub(r"\n{3,}", "\n\n", result)


def looks_structured(text: str) -> bool:
    """Whether the text already carries list or paragraph structure."""
    return bool(re.search(r"^\s*(?:\d+\.|[-*•])\s", text, re.MULTILINE)) or "\n\n" in text
