"""Stage 7b - email shape.

Dictating an email produces one long run of words: "hi Sarah just wanted to
check if we're still on for tomorrow thanks Kevin". What the user wants is the
shape of an email - greeting on its own line, body, sign-off, name.

This is deterministic and conservative. It only restructures when it can see
the *landmarks*: a greeting at the start, a sign-off near the end, or an
explicit "email X saying" preamble. Everything between them is left completely
untouched - no rewording, no added pleasantries. Inventing an email body is the
one thing this stage must never do.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

GREETINGS = (
    "hi", "hello", "hey", "dear", "good morning", "good afternoon", "good evening",
)
SIGN_OFFS = (
    "thanks", "thank you", "many thanks", "thanks a lot", "cheers", "regards",
    "best regards", "kind regards", "warm regards", "best", "sincerely",
    "yours sincerely", "yours faithfully", "talk soon", "speak soon",
)

# "email Sarah saying ..." / "send Rahul an email saying ..."
_PREAMBLE_RE = re.compile(
    r"^\s*(?:please\s+)?(?:write|send|draft|compose|email)\s+"
    r"(?:an?\s+)?(?:email\s+)?(?:to\s+)?(?P<who>[A-Z][\w'-]+|\w+)?\s*"
    r"(?:an?\s+email\s+)?(?:saying|that says|with|telling (?:them|him|her))\s+",
    re.IGNORECASE,
)
_GREETING_RE = re.compile(
    r"^\s*(?P<greet>" + "|".join(GREETINGS) + r")\b[\s,]*(?P<who>[A-Z][\w'-]+)?[\s,]*",
    re.IGNORECASE,
)
_SIGNOFF_RE = re.compile(
    r"(?<![\w])(?P<signoff>" + "|".join(sorted(SIGN_OFFS, key=len, reverse=True)) + r")"
    r"[\s,]*(?P<who>[A-Z][\w'-]+)?\s*[.!]?\s*$",
    re.IGNORECASE,
)


@dataclass
class EmailResult:
    text: str
    applied: bool = False
    recipient: str = ""
    signer: str = ""


def _titlecase_name(name: str) -> str:
    return name[:1].upper() + name[1:] if name else name


def format_email(text: str, enabled: bool = True, is_email_app: bool = False) -> EmailResult:
    """Give a dictated email its structure, without touching its words."""
    if not enabled or not text.strip() or "\n\n" in text:
        return EmailResult(text)

    body = text.strip()
    recipient = ""

    # "email Sarah saying ..." - strip the instruction, keep the message.
    preamble = _PREAMBLE_RE.match(body)
    if preamble:
        recipient = _titlecase_name(preamble.group("who") or "")
        body = body[preamble.end() :].strip()

    greeting_text = ""
    greeting = _GREETING_RE.match(body)
    if greeting:
        who = greeting.group("who") or recipient
        greet = greeting.group("greet")
        greeting_text = _titlecase_name(greet) + (f" {_titlecase_name(who)}" if who else "")
        body = body[greeting.end() :].strip()
        if who and not recipient:
            recipient = who

    signoff_text = ""
    signer = ""
    signoff = _SIGNOFF_RE.search(body)
    if signoff:
        signer = _titlecase_name(signoff.group("who") or "")
        signoff_text = _titlecase_name(signoff.group("signoff"))
        body = body[: signoff.start()].strip().rstrip(",")

    landmarks = sum(bool(x) for x in (greeting_text, signoff_text, preamble))
    # One landmark alone is not an email. "Hi Sarah" on its own is a message,
    # and "thanks Kevin" on its own is a sentence.
    if landmarks < 2 and not (is_email_app and landmarks >= 1):
        return EmailResult(text)
    if not body:
        return EmailResult(text)

    body = body[:1].upper() + body[1:]
    if body[-1] not in ".!?":
        body += "."

    parts: list[str] = []
    if greeting_text:
        parts.append(greeting_text + ",")
    parts.append(body)
    if signoff_text:
        parts.append(signoff_text + "," + (f"\n{signer}" if signer else ""))

    return EmailResult("\n\n".join(parts), True, recipient, signer)


def looks_like_email(text: str) -> bool:
    """Whether the text carries email landmarks, for the LLM gate."""
    if not text.strip():
        return False
    has_greeting = bool(_GREETING_RE.match(text.strip()))
    has_signoff = bool(_SIGNOFF_RE.search(text.strip()))
    return (has_greeting and has_signoff) or bool(_PREAMBLE_RE.match(text.strip()))
