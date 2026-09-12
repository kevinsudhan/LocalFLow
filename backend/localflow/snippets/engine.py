"""Voice-triggered snippets.

"Insert my email signature" should produce the signature verbatim - not a
paraphrase of it - so a snippet match short-circuits the rewriting pipeline
entirely.  Matching is whole-utterance by default (with optional leading verbs
stripped) so a snippet cannot fire from a passing mention.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Leading verbs users naturally say before a trigger.
_LEAD_IN = re.compile(
    r"^(?:please\s+)?(?:insert|add|paste|drop in|put in|give me|use)\s+"
    r"(?:my|the|a|an)?\s*",
    re.IGNORECASE,
)
_TRAILING = re.compile(r"\s*(?:here|now|please)\s*$", re.IGNORECASE)
MAX_TRIGGER_WORDS = 8


@dataclass
class SnippetMatch:
    trigger: str
    name: str
    expansion: str
    mode: str
    remainder: str = ""


def _normalise(text: str) -> str:
    cleaned = re.sub(r"[^\w\s']", " ", (text or "").lower())
    return " ".join(cleaned.split())


class SnippetEngine:
    def __init__(self) -> None:
        self._snippets: list[dict] = []

    def load(self, snippets: list[dict]) -> None:
        # Longest trigger first so "my email signature" beats "signature".
        self._snippets = sorted(
            [s for s in (snippets or []) if s.get("enabled", True) and s.get("trigger")],
            key=lambda s: -len(s["trigger"]),
        )

    @property
    def size(self) -> int:
        return len(self._snippets)

    @property
    def triggers(self) -> list[str]:
        return [s["trigger"] for s in self._snippets]

    def match(self, text: str, enabled: bool = True) -> SnippetMatch | None:
        if not enabled or not self._snippets or not text:
            return None
        normalised = _normalise(text)
        if not normalised:
            return None
        stripped = _TRAILING.sub("", _LEAD_IN.sub("", normalised)).strip()

        for snippet in self._snippets:
            trigger = _normalise(snippet["trigger"])
            if not trigger:
                continue
            for candidate in (normalised, stripped):
                if candidate == trigger:
                    return SnippetMatch(
                        trigger=snippet["trigger"],
                        name=snippet.get("name", snippet["trigger"]),
                        expansion=snippet["expansion"],
                        mode=snippet.get("mode", "replace_all"),
                    )
            # Inline snippets may appear at the start of a longer utterance.
            if snippet.get("mode") == "inline" and stripped.startswith(trigger + " "):
                return SnippetMatch(
                    trigger=snippet["trigger"],
                    name=snippet.get("name", snippet["trigger"]),
                    expansion=snippet["expansion"],
                    mode="inline",
                    remainder=stripped[len(trigger) :].strip(),
                )
        return None


DEFAULT_SNIPPETS: tuple[dict, str, str] = ()


def starter_snippets(display_name: str = "") -> list[dict]:
    """Examples created on first run so the feature is discoverable."""
    name = display_name or "Your Name"
    return [
        {
            "name": "Email signature",
            "trigger": "email signature",
            "expansion": f"Best regards,\n{name}",
            "mode": "replace_all",
        },
        {
            "name": "Meeting follow-up",
            "trigger": "meeting follow up",
            "expansion": (
                "Thanks for your time today. Here is a short summary of what we agreed:"
            ),
            "mode": "replace_all",
        },
    ]
