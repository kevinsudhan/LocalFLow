"""Style profiles.

A style changes *how* text is written, never *what* it says.  Each profile is a
short instruction handed to the LLM plus a set of hard limits that the
validation stage enforces, so a style can never be used as a licence to add
content.
"""
from __future__ import annotations

from dataclasses import dataclass

NEUTRAL = "neutral"
PROFESSIONAL = "professional"
CASUAL = "casual"
CONCISE = "concise"
DEVELOPER = "developer"


@dataclass(frozen=True)
class StyleProfile:
    key: str
    label: str
    description: str
    instruction: str
    allow_contractions: bool = True
    allow_reordering: bool = False


PROFILES: dict[str, StyleProfile] = {
    NEUTRAL: StyleProfile(
        NEUTRAL,
        "Neutral",
        "Clean up the text without changing its voice.",
        "Keep the speaker's own wording and register. Fix only grammar, "
        "punctuation, capitalisation and obvious disfluency.",
    ),
    PROFESSIONAL: StyleProfile(
        PROFESSIONAL,
        "Professional",
        "Polished business writing for email and client communication.",
        "Write in a polished, professional register suitable for business email. "
        "Use complete sentences and courteous phrasing. Do not add greetings, "
        "sign-offs, or any information the speaker did not say.",
        allow_contractions=False,
        allow_reordering=True,
    ),
    CASUAL: StyleProfile(
        CASUAL,
        "Casual",
        "Relaxed conversational tone for chat apps.",
        "Keep it conversational and relaxed, the way a person types in a chat "
        "app. Short sentences are fine. Do not add emoji or filler.",
    ),
    CONCISE: StyleProfile(
        CONCISE,
        "Concise",
        "Say the same thing in fewer words.",
        "Tighten the wording. Remove redundancy and hedging while preserving "
        "every fact, name, number and request. Never drop a requirement.",
        allow_reordering=True,
    ),
    DEVELOPER: StyleProfile(
        DEVELOPER,
        "Developer",
        "Preserves identifiers, paths, commands and casing.",
        "This text is going into a code editor or terminal. Preserve identifiers, "
        "file paths, URLs, command names, package names and their exact casing "
        "(camelCase, snake_case, PascalCase, kebab-case). Do not translate "
        "technical terms into prose and do not add explanation. When the speaker "
        "says an identifier as separate words - \"get customer\", \"user id\", "
        "\"shipment service\" - join it in the convention the surrounding code "
        "uses (getCustomer, userId, shipment_service). Capitalise product and "
        "library names correctly (supabase -> Supabase, postgres -> Postgres).",
    ),
}


def get(style: str | None) -> StyleProfile:
    return PROFILES.get((style or NEUTRAL).lower(), PROFILES[NEUTRAL])


def instruction_for(style: str | None, category: str = "") -> str:
    profile = get(style)
    extra = CATEGORY_HINTS.get(category, "")
    return (profile.instruction + (" " + extra if extra else "")).strip()


CATEGORY_HINTS: dict[str, str] = {
    "email": "Format as email body text. Preserve paragraph breaks.",
    "messaging": "Format as a single chat message. Avoid formal salutations.",
    "document": "Format as document prose with proper paragraphs.",
    "code": "If the speaker described code, keep their terminology verbatim.",
    "terminal": "Output the command exactly as spoken. Change nothing else.",
    "chat": "Format as a direct prompt or message. No preamble.",
}


def payload() -> list[dict]:
    return [
        {"key": p.key, "label": p.label, "description": p.description}
        for p in PROFILES.values()
    ]
