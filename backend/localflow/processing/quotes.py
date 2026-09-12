"""Stage 6b - quotation and reported speech.

Two distinct problems, handled differently on purpose:

* **Explicit** - "quote I'll send it Friday end quote". The speaker said where
  the quotation starts and ends, so this is a deterministic rewrite with no
  judgement involved.
* **Reported speech** - "John said I'll send it tomorrow". Deciding where the
  quotation *ends* requires understanding the sentence, and getting it wrong
  puts words in someone's mouth. So this stage only detects the pattern and
  reports it; the LLM stage does the rewrite, and validation checks that no
  content was invented.

That split is deliberate. A regex that guesses quotation boundaries would
produce confident, wrong attributions - the worst possible failure for a tool
that writes on your behalf.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Verbs that introduce reported speech.
SPEECH_VERBS = (
    "said", "says", "say", "told", "tells", "asked", "asks", "replied",
    "replies", "answered", "wrote", "writes", "mentioned", "mentions",
    "explained", "added", "noted", "remarked", "commented", "responded",
)
# A speaker: a name, a pronoun, or a role.
_SUBJECT = r"(?:[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?|he|she|they|i|we|you|it|everyone|someone)"

_REPORTED_RE = re.compile(
    r"(?<![\w])(?P<subject>" + _SUBJECT + r")\s+(?P<verb>" + "|".join(SPEECH_VERBS) + r")\s+"
    r"(?P<speech>[^,\"'“]{6,}?)(?=$|[.!?])",
    re.IGNORECASE,
)

# "quote ... end quote" / "open quote ... close quote" / "quote ... unquote"
_EXPLICIT_RE = re.compile(
    r"(?<![\w])(?:open\s+quote|quote)\s+(?P<body>.+?)\s+(?:end\s+quote|close\s+quote|unquote)(?![\w])",
    re.IGNORECASE | re.DOTALL,
)
# An opened quotation the speaker never closed.
_DANGLING_RE = re.compile(
    r"(?<![\w])(?:open\s+quote|quote)\s+(?P<body>.+)$", re.IGNORECASE | re.DOTALL
)

# Words after which "quote" is a noun, not a command.
_QUOTE_AS_NOUN = {"the", "a", "an", "this", "that", "his", "her", "my", "your", "their", "send"}


@dataclass
class QuoteResult:
    text: str
    applied: list[str] = field(default_factory=list)
    reported_speech: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.applied)


def _smart_wrap(body: str) -> str:
    body = body.strip().strip(",")
    if not body:
        return ""
    if body[0].islower():
        body = body[0].upper() + body[1:]
    if body[-1] not in ".!?":
        body += "."
    return "“" + body + "”"


def apply_explicit_quotes(text: str) -> QuoteResult:
    """Rewrite spoken quote delimiters into real quotation marks."""
    applied: list[str] = []

    def replace(match: re.Match) -> str:
        # "send me the quote by Friday" is a noun, not a delimiter.
        preceding = text[: match.start()].rstrip()
        last_word = re.findall(r"[\w']+$", preceding)
        if last_word and last_word[-1].lower() in _QUOTE_AS_NOUN:
            return match.group(0)
        wrapped = _smart_wrap(match.group("body"))
        if not wrapped:
            return match.group(0)
        applied.append(match.group("body").strip())
        return wrapped

    result = _EXPLICIT_RE.sub(replace, text)

    if not applied:
        match = _DANGLING_RE.search(result)
        if match:
            preceding = result[: match.start()].rstrip()
            last_word = re.findall(r"[\w']+$", preceding)
            body = match.group("body").strip()
            # Only treat a dangling "quote" as a delimiter when what follows
            # is substantial; otherwise it is almost certainly the noun.
            if (
                not (last_word and last_word[-1].lower() in _QUOTE_AS_NOUN)
                and len(body.split()) >= 3
            ):
                wrapped = _smart_wrap(body)
                result = result[: match.start()] + wrapped
                applied.append(body)

    return QuoteResult(result, applied)


def find_reported_speech(text: str) -> list[str]:
    """Detect 'X said <something>' without rewriting it.

    The result feeds the LLM gate. Nothing here changes the text, because
    choosing where a quotation ends is exactly the judgement a regex should not
    be making on someone's behalf.
    """
    if '"' in text or "“" in text:
        return []            # already quoted; leave it alone
    found: list[str] = []
    for match in _REPORTED_RE.finditer(text):
        speech = match.group("speech").strip()
        if len(speech.split()) < 3:
            continue
        # "he said that ..." is indirect speech and takes no quotation marks.
        if re.match(r"^(?:that|if|whether|to|about|it|nothing|so)\b", speech, re.IGNORECASE):
            continue
        found.append(f"{match.group('subject')} {match.group('verb')} {speech}")
    return found


def apply(text: str, enabled: bool = True) -> QuoteResult:
    if not enabled or not text.strip():
        return QuoteResult(text)
    result = apply_explicit_quotes(text)
    result.reported_speech = find_reported_speech(result.text)
    return result
