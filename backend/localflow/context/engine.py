"""The context engine.

Rust collects the raw facts (foreground window, selection, text around the
caret) via UI Automation and hands them over; this module turns them into
decisions: which application category, which style, whether the dictation
continues an existing sentence, and what the LLM is allowed to see.

The safety rule from the spec is enforced here: context may *disambiguate*
("doctor Agarwal" -> "Dr. Agarwal" because that spelling is already on screen)
but must never *contribute* content.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .apps import AppIdentity, GENERAL, TERMINAL, classify

MAX_CONTEXT_CHARS = 600


@dataclass
class ContextSnapshot:
    """Everything known about where the text is going."""

    exe: str = ""
    window_title: str = ""
    url: str = ""
    selected_text: str = ""
    text_before: str = ""
    text_after: str = ""
    control_type: str = ""
    is_password: bool = False
    has_uia_text: bool = False
    monitor: dict = field(default_factory=dict)
    caret: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict | None) -> "ContextSnapshot":
        data = data or {}
        return cls(
            exe=str(data.get("exe", "") or ""),
            window_title=str(data.get("window_title", "") or ""),
            url=str(data.get("url", "") or ""),
            selected_text=str(data.get("selected_text", "") or "")[:4000],
            text_before=str(data.get("text_before", "") or "")[-MAX_CONTEXT_CHARS:],
            text_after=str(data.get("text_after", "") or "")[:MAX_CONTEXT_CHARS],
            control_type=str(data.get("control_type", "") or ""),
            is_password=bool(data.get("is_password", False)),
            has_uia_text=bool(data.get("has_uia_text", False)),
            monitor=data.get("monitor") or {},
            caret=data.get("caret") or {},
        )


@dataclass
class ResolvedContext:
    snapshot: ContextSnapshot
    identity: AppIdentity
    continuation: str          # "new_document" | "new_paragraph" | "new_sentence" | "mid_sentence"
    replace_selection: bool
    style: str
    llm_enabled: bool
    injection_method: str
    proper_nouns: list[str] = field(default_factory=list)

    @property
    def continues_sentence(self) -> bool:
        return self.continuation == "mid_sentence"

    @property
    def category(self) -> str:
        return self.identity.category

    def as_dict(self) -> dict:
        return {
            "exe": self.snapshot.exe,
            "app_name": self.identity.app_name,
            "category": self.identity.category,
            "window_title": self.snapshot.window_title,
            "url": self.snapshot.url,
            "continuation": self.continuation,
            "replace_selection": self.replace_selection,
            "style": self.style,
            "llm_enabled": self.llm_enabled,
            "injection_method": self.injection_method,
            "has_selection": bool(self.snapshot.selected_text),
            "has_surrounding_text": bool(self.snapshot.text_before or self.snapshot.text_after),
            "matched_on": self.identity.matched_on,
        }


_SENTENCE_END = re.compile(r"[.!?…][\"')\]]*\s*$")
_LIST_ITEM = re.compile(r"(?:^|\n)\s*(?:[-*•]|\d+[.)])\s+[^\n]*$")
_PROPER_NOUN = re.compile(r"\b(?:[A-Z][a-z]{2,}|[A-Z]{2,})(?:\.[A-Za-z]+)?\b")
_STOP_PROPER = {
    "The", "This", "That", "There", "These", "Those", "Hi", "Hello", "Dear",
    "Thanks", "Thank", "Best", "Regards", "Please", "From", "Sent", "Subject",
    "And", "But", "For", "With", "When", "What", "Where", "Why", "How",
}


def analyse_continuation(text_before: str, has_uia_text: bool) -> str:
    """How the dictated text joins what is already in the field."""
    if not has_uia_text:
        # Without UI Automation text we cannot know; assume a fresh sentence,
        # which is the safe default (capitalised, self-terminating).
        return "new_sentence"
    if not text_before.strip():
        return "new_document"
    tail = text_before.rstrip(" \t")
    if tail.endswith("\n\n") or text_before.endswith("\n\n"):
        return "new_paragraph"
    if _LIST_ITEM.search(text_before):
        return "mid_sentence" if not _SENTENCE_END.search(text_before) else "new_sentence"
    if text_before.endswith("\n"):
        return "new_paragraph"
    if _SENTENCE_END.search(text_before):
        return "new_sentence"
    if tail.endswith((",", ";", ":", "-", "—", "(", "\"", "'")):
        return "mid_sentence"
    # An unfinished sentence has to look like one.
    #
    # Treating any trailing letter as mid-sentence made that the default for
    # almost every field, and a chat composer that reports surrounding page
    # text through UI Automation then lowercased the first word of every
    # dictation: "What is the time" arrived as "what is the time". The two
    # mistakes are not symmetric - wrongly lowercasing a sentence opening is
    # visible every time, wrongly capitalising a continuation costs one
    # letter - so the tie now breaks towards a new sentence, which is what
    # the no-UIA branch above already assumes.
    if tail and tail[-1].isalnum():
        fragment = _SENTENCE_END.split(tail)[-1].strip()
        return "mid_sentence" if len(fragment.split()) >= 2 else "new_sentence"
    return "new_sentence"


def extract_proper_nouns(*texts: str, limit: int = 24) -> list[str]:
    """Capitalised words visible around the caret.

    Used only to *disambiguate* a spoken name against a spelling already on
    screen - never to add anything to the output.
    """
    seen: list[str] = []
    for text in texts:
        if not text:
            continue
        for match in _PROPER_NOUN.finditer(text):
            word = match.group(0)
            if word in _STOP_PROPER or len(word) < 3:
                continue
            if word not in seen:
                seen.append(word)
            if len(seen) >= limit:
                return seen
    return seen


class ContextEngine:
    def __init__(self) -> None:
        self.profiles: list[dict] = []
        self.default_style = "neutral"
        self.use_context = True

    def configure(self, profiles: list[dict], default_style: str, use_context: bool) -> None:
        self.profiles = profiles or []
        self.default_style = default_style or "neutral"
        self.use_context = use_context

    def resolve(
        self,
        snapshot: ContextSnapshot,
        style_override: str = "",
        llm_globally_enabled: bool = True,
    ) -> ResolvedContext:
        identity = classify(
            exe=snapshot.exe,
            title=snapshot.window_title,
            url=snapshot.url,
            profiles=self.profiles,
        )
        style = style_override or identity.style or self.default_style
        if identity.category == GENERAL and not style_override:
            style = self.default_style

        continuation = (
            analyse_continuation(snapshot.text_before, snapshot.has_uia_text)
            if self.use_context
            else "new_sentence"
        )
        # Dictating over a selection replaces it; that is what selecting means.
        replace_selection = bool(snapshot.selected_text.strip())

        llm_enabled = llm_globally_enabled and identity.llm_enabled
        if snapshot.is_password:
            llm_enabled = False

        proper_nouns = (
            extract_proper_nouns(snapshot.text_before, snapshot.text_after, snapshot.selected_text)
            if self.use_context and identity.category != TERMINAL
            else []
        )

        return ResolvedContext(
            snapshot=snapshot,
            identity=identity,
            continuation=continuation,
            replace_selection=replace_selection,
            style=style,
            llm_enabled=llm_enabled,
            injection_method=identity.injection_method,
            proper_nouns=proper_nouns,
        )

    @staticmethod
    def context_for_prompt(ctx: ResolvedContext, limit: int = 320) -> str:
        """The slice of surrounding text the LLM is allowed to see."""
        before = (ctx.snapshot.text_before or "")[-limit:]
        after = (ctx.snapshot.text_after or "")[:120]
        if not before and not after:
            return ""
        parts = []
        if before:
            parts.append("...text before the cursor: " + before.strip())
        if after:
            parts.append("...text after the cursor: " + after.strip())
        return "\n".join(parts)
