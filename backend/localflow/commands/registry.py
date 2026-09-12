"""Voice commands - a strict allowlist.

Commands must never fire by accident in the middle of ordinary dictation, so
recognition is deliberately narrow:

* the utterance must match a registered pattern **in full** (after
  normalisation), never as a substring;
* the utterance must be short;
* every command is declared here, and anything not declared here is dictation.

There is no code path that executes arbitrary text.  Commands map to named
actions that the host implements; nothing is ever passed to a shell.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

MAX_COMMAND_WORDS = 8

# Named actions the host knows how to perform.
UNDO = "undo"
DELETE_LAST_SENTENCE = "delete_last_sentence"
SELECT_LAST = "select_last"
NEW_PARAGRAPH = "new_paragraph"
NEW_LINE = "new_line"
REWRITE = "rewrite"
RESTYLE = "restyle"
TRANSLATE = "translate"
CANCEL = "cancel"
SNIPPET = "snippet"
DELETE_LAST = "delete_last"
SELECT_LAST_WORD = "select_last_word"
CAPITALIZE_LAST = "capitalize_last"
MAKE_LIST = "make_list"
REPLACE_LAST = "replace_last"

# Languages the translate command will accept.  An allowlist, not free text.
TRANSLATE_TARGETS = {
    "english": "English",
    "tamil": "Tamil",
    "hindi": "Hindi",
    "french": "French",
    "german": "German",
    "spanish": "Spanish",
    "japanese": "Japanese",
    "arabic": "Arabic",
    "malayalam": "Malayalam",
    "telugu": "Telugu",
    "kannada": "Kannada",
}

RESTYLE_TARGETS = {
    "formal": "Rewrite in a formal, professional register.",
    "professional": "Rewrite in a formal, professional register.",
    "casual": "Rewrite in a relaxed, conversational register.",
    "friendly": "Rewrite in a warm, friendly register.",
    "concise": "Tighten the wording without losing any fact or request.",
    "shorter": "Tighten the wording without losing any fact or request.",
    "longer": "Expand into full sentences without adding new information.",
    "polite": "Rewrite more politely without adding new information.",
}


@dataclass
class Command:
    action: str
    argument: str = ""
    instruction: str = ""
    matched: str = ""
    payload: dict = field(default_factory=dict)


@dataclass
class _Rule:
    pattern: re.Pattern[str]
    build: Callable[[re.Match], Command | None]


def _normalise(text: str) -> str:
    cleaned = re.sub(r"[^\w\s']", " ", (text or "").lower())
    return " ".join(cleaned.split())


def _simple(action: str) -> Callable[[re.Match], Command]:
    def build(match: re.Match) -> Command:
        return Command(action=action, matched=match.group(0))

    return build


def _restyle(match: re.Match) -> Command | None:
    target = match.group("target").strip()
    instruction = RESTYLE_TARGETS.get(target)
    if not instruction:
        return None
    return Command(RESTYLE, argument=target, instruction=instruction, matched=match.group(0))


def _translate(match: re.Match) -> Command | None:
    target = match.group("target").strip()
    language = TRANSLATE_TARGETS.get(target)
    if not language:
        return None
    return Command(
        TRANSLATE,
        argument=language,
        instruction=(
            f"Translate the text into {language}. Preserve names, numbers and dates "
            "exactly. Output only the translation."
        ),
        matched=match.group(0),
    )


def _replace(match: re.Match) -> Command | None:
    """"replace that with <text>" - the replacement is data, never a command."""
    replacement = (match.group("text") or "").strip()
    if not replacement or len(replacement.split()) > 12:
        return None
    return Command(
        REPLACE_LAST,
        argument=replacement,
        matched=match.group(0),
        payload={"text": replacement},
    )


RULES: tuple[_Rule, ...] = (
    _Rule(re.compile(r"^(?:undo(?: that| last dictation| dictation)?|take that back)$"),
          _simple(UNDO)),
    _Rule(re.compile(r"^(?:delete|remove|scratch) (?:the )?last (?:sentence|line)$"),
          _simple(DELETE_LAST_SENTENCE)),
    _Rule(re.compile(r"^select (?:the )?last (?:sentence|line|dictation)$"), _simple(SELECT_LAST)),
    _Rule(
        re.compile(r"^(?:delete|remove|scratch) (?:that|this|it)$"), _simple(DELETE_LAST)
    ),
    _Rule(
        re.compile(r"^(?:delete|remove) (?:the )?last (?:word|few words)$"),
        _simple(DELETE_LAST),
    ),
    _Rule(re.compile(r"^select (?:that|this|it)$"), _simple(SELECT_LAST)),
    _Rule(re.compile(r"^select (?:the )?last word$"), _simple(SELECT_LAST_WORD)),
    _Rule(
        re.compile(r"^capitali[sz]e (?:that|this|it|the last word)$"),
        _simple(CAPITALIZE_LAST),
    ),
    _Rule(
        re.compile(r"^make (?:this|that|it) (?:a |into a )?(?:list|bullet list|numbered list)$"),
        _simple(MAKE_LIST),
    ),
    _Rule(
        re.compile(r"^replace (?:that|this|it) with (?P<text>.+)$"),
        _replace,
    ),
    _Rule(re.compile(r"^new paragraph$"), _simple(NEW_PARAGRAPH)),
    _Rule(re.compile(r"^new line$"), _simple(NEW_LINE)),
    _Rule(re.compile(r"^(?:cancel|never mind|nevermind|forget (?:it|that))$"), _simple(CANCEL)),
    _Rule(re.compile(r"^(?:rewrite|redo|clean up|polish) (?:that|this|it)$"), _simple(REWRITE)),
    _Rule(
        re.compile(r"^(?:make (?:that|this|it)|rewrite (?:that|this|it)) (?P<target>[a-z]+)$"),
        _restyle,
    ),
    _Rule(re.compile(r"^(?:make (?:that|this|it) more) (?P<target>[a-z]+)$"), _restyle),
    _Rule(
        re.compile(
            r"^translate (?:this|that|it)(?: in ?to| to)? (?P<target>[a-z]+)$"
        ),
        _translate,
    ),
)


def detect(text: str, enabled: bool = True) -> Command | None:
    """Return a command only when the whole utterance is that command."""
    if not enabled or not text:
        return None
    normalised = _normalise(text)
    if not normalised or len(normalised.split()) > MAX_COMMAND_WORDS:
        return None
    for rule in RULES:
        match = rule.pattern.match(normalised)
        if match:
            command = rule.build(match)
            if command is not None:
                return command
    return None


def describe() -> list[dict]:
    """Human-readable list for the Settings > Keyboard screen."""
    return [
        {"phrase": "undo that", "action": UNDO, "description": "Remove the text just inserted."},
        {"phrase": "delete last sentence", "action": DELETE_LAST_SENTENCE,
         "description": "Delete the last sentence of the previous dictation."},
        {"phrase": "select last sentence", "action": SELECT_LAST,
         "description": "Select the previous dictation so you can replace it."},
        {"phrase": "new paragraph", "action": NEW_PARAGRAPH, "description": "Insert a blank line."},
        {"phrase": "new line", "action": NEW_LINE, "description": "Insert a line break."},
        {"phrase": "delete that", "action": DELETE_LAST,
         "description": "Remove the text just inserted."},
        {"phrase": "select that", "action": SELECT_LAST,
         "description": "Select the previous dictation."},
        {"phrase": "capitalize that", "action": CAPITALIZE_LAST,
         "description": "Capitalise the previous dictation."},
        {"phrase": "make this a list", "action": MAKE_LIST,
         "description": "Reformat the previous dictation as a list."},
        {"phrase": "replace that with ...", "action": REPLACE_LAST,
         "description": "Swap the previous dictation for what you say next."},
        {"phrase": "rewrite that", "action": REWRITE,
         "description": "Re-run the previous dictation through the local model."},
        {"phrase": "make that formal / casual / concise", "action": RESTYLE,
         "description": "Restyle the previous dictation."},
        {"phrase": "translate this to Tamil", "action": TRANSLATE,
         "description": "Translate the previous dictation into a supported language."},
        {"phrase": "cancel", "action": CANCEL, "description": "Discard this dictation."},
    ]
