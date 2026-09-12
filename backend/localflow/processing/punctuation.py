"""Stage 6 - punctuation, capitalisation and sentence shape.

Whisper already punctuates reasonably, so this stage repairs rather than
invents: it finishes unterminated sentences, turns obvious inversions into
questions, and fixes casing.  Question inference is deliberately narrow because
a wrong "?" changes the meaning of a sentence.
"""
from __future__ import annotations

import re

from .tokens import CLOSERS, TERMINAL, split_sentences, tokenize

AUXILIARIES = {
    "can", "could", "will", "would", "do", "does", "did", "is", "are", "am",
    "was", "were", "shall", "should", "may", "might", "have", "has", "had",
    "must", "ca", "wo",
}
WH_WORDS = {"who", "what", "when", "where", "why", "how", "which", "whose", "whom"}
SUBJECTS = {
    "i", "you", "he", "she", "it", "we", "they", "there", "this", "that",
    "these", "those", "everyone", "anyone", "someone", "somebody", "anybody",
    "a", "an", "the", "my", "your", "his", "her", "our", "their", "its", "one",
}
# "how" and "which" start statements often enough to be excluded from the
# wh-question rule; "How we ship" is not a question.
WH_QUESTION_SAFE = {"who", "what", "when", "where", "why", "whose", "whom"}

_LIST_ITEM_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")
I_FORMS = re.compile(r"\bi\b(?=(?:'(?:m|ve|ll|d|re))?\b)")
_ACRONYM = re.compile(r"^[A-Z0-9][A-Z0-9.&/-]{1,}$")


def looks_like_question(sentence: str) -> bool:
    tokens = [t.lower for t in tokenize(sentence) if t.is_word or t.is_number]
    if len(tokens) < 2:
        return False
    first, second = tokens[0], tokens[1]
    if first in AUXILIARIES and second in SUBJECTS:
        return True
    if first in WH_QUESTION_SAFE and any(t in AUXILIARIES for t in tokens[1:5]):
        return True
    if first in ("any", "anyone") and second in ("questions", "thoughts"):
        return True
    return False


def ensure_terminal_punctuation(text: str, auto_question: bool = True) -> str:
    """Give every sentence an ending, adding '?' only on clear inversions."""
    if not text.strip():
        return text
    out_blocks: list[str] = []
    for block in text.split("\n"):
        if not block.strip():
            out_blocks.append(block)
            continue
        sentences = split_sentences(block)
        if not sentences:
            out_blocks.append(block)
            continue
        # A list item is a fragment by design; forcing a period onto
        # "1. Fix Outlook" makes the list look wrong.
        if _LIST_ITEM_RE.match(block):
            out_blocks.append(block.rstrip())
            continue
        fixed: list[str] = []
        for sentence in sentences:
            stripped = sentence.rstrip()
            # A closing quote or bracket may sit after the terminator:
            # '"I'll send it Friday."' is finished, not missing a full stop.
            core = stripped.rstrip(CLOSERS)
            if core and core[-1] in TERMINAL:
                if auto_question and core[-1] == "." and looks_like_question(core):
                    stripped = stripped.replace(core, core[:-1] + "?", 1)
                fixed.append(stripped)
                continue
            # ":" is kept: it is how a list lead-in ends.
            if stripped.endswith((",", ";", "-", "—")):
                stripped = stripped[:-1].rstrip()
            if not stripped:
                continue
            if _ends_open(stripped):
                fixed.append(stripped)
                continue
            fixed.append(stripped + ("?" if auto_question and looks_like_question(stripped) else "."))
        out_blocks.append(" ".join(fixed))
    return "\n".join(out_blocks)


def _ends_open(sentence: str) -> bool:
    """True for fragments that should not be force-terminated (list items, code)."""
    return sentence.endswith((":", "```", "{", "(", "[")) or bool(
        re.search(r"[=+\-*/<>|&]$", sentence)
    )


def capitalize(text: str, enabled: bool = True, continue_sentence: bool = False) -> str:
    """Sentence-case the text, preserving acronyms and existing capitals."""
    if not enabled or not text:
        return text

    result = I_FORMS.sub("I", text)

    out: list[str] = []
    capitalize_next = not continue_sentence
    for idx, char in enumerate(result):
        if capitalize_next and char.isalpha():
            out.append(char.upper())
            capitalize_next = False
            continue
        if capitalize_next and char.isdigit():
            capitalize_next = False
        out.append(char)
        if char in TERMINAL:
            capitalize_next = True
        elif char == "\n":
            capitalize_next = True
        elif char not in " \t\"'“‘([{" and not char.isspace():
            capitalize_next = False
    return "".join(out)


def lowercase_continuation(text: str) -> str:
    """Lowercase the first word when dictation continues an existing sentence.

    Only applies to ordinary words - a name, acronym or "I" keeps its capital.
    """
    match = re.match(r"^(\s*)([A-Z][a-z']+)(\b)", text)
    if not match:
        return text
    word = match.group(2)
    if word == "I" or _ACRONYM.match(word):
        return text
    return match.group(1) + word[0].lower() + word[1:] + text[match.end() :]


def strip_leading_terminator(text: str) -> str:
    return re.sub(r"^\s*[.,;:!?]+\s*", "", text)


def normalize_quotes(text: str) -> str:
    text = re.sub(r'\s+"', ' "', text)
    text = re.sub(r'"\s+([,.;:!?])', r'"\1', text)
    return text


def apply(
    text: str,
    auto_punctuation: bool = True,
    smart_capitalization: bool = True,
    continue_sentence: bool = False,
) -> str:
    if not text.strip():
        return ""
    result = text
    if auto_punctuation:
        result = ensure_terminal_punctuation(result)
    result = capitalize(result, smart_capitalization, continue_sentence=continue_sentence)
    if continue_sentence:
        result = lowercase_continuation(result)
    return normalize_quotes(result)
