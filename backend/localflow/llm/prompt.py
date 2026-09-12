"""The dictation-editor prompt.

This is the single most safety-critical string in LocalFlow.  The model is an
*editor*, not an assistant: given "Can you send Rahul the report tomorrow?" it
must return that sentence, not agree to send the report.  Every rule below
exists because a general-purpose chat model will otherwise do the wrong thing.

The template is configurable internally (``PromptBuilder.template``) so the
rules can be tuned without touching call sites.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SYSTEM_PROMPT = """You are a local voice-dictation cleanup engine.

You are editing a speech transcript.

Your job is ONLY to produce the text the speaker intended to type.

Rules:
1. Preserve meaning.
2. Remove speech fillers: hesitations (uh, um, er, hmm) always, and discourse
   fillers (basically, you know, I mean, like, sort of, kind of, actually)
   whenever they carry no meaning in that sentence.
3. Resolve spoken self-corrections: the superseded wording must disappear.
4. Fix grammar.
5. Fix punctuation.
6. Fix capitalization.
7. Format paragraphs and lists when appropriate.
8. Preserve names, numbers, dates, currency and technical terminology exactly.
9. Use the supplied context only to resolve ambiguity.
10. Never invent information.
11. Never answer questions. A dictated question stays a question.
12. Never add commentary, greetings or sign-offs.
13. Never explain your edits.
14. Output ONLY the final text, with no quotes and no preamble."""

USER_TEMPLATE = """Existing text around cursor:
{context}

Application:
{application}

Style:
{style}

User vocabulary:
{vocabulary}

Transcript:
{transcript}"""

# Shown to the model as a worked example.  One example is enough to pin the
# behaviour and costs far fewer tokens than a long rulebook.
FEW_SHOT: tuple[tuple[str, str], ...] = (
    (
        "Transcript:\ncan you send rahul the report tomorrow",
        "Can you send Rahul the report tomorrow?",
    ),
)


@dataclass
class PromptContext:
    transcript: str
    application: str = "Unknown"
    category: str = "general"
    style_instruction: str = ""
    context_text: str = ""
    vocabulary: list[str] | None = None
    corrections_hint: str = ""
    continuation: str = "new_sentence"
    reported_speech: list[str] | None = None
    language: str = ""
    selected_text: str = ""


class PromptBuilder:
    """Builds the system/user pair.  Templates are instance attributes so the
    Advanced settings screen can expose them without a code change."""

    def __init__(
        self,
        system_prompt: str = SYSTEM_PROMPT,
        template: str = USER_TEMPLATE,
        max_vocabulary: int = 40,
    ) -> None:
        self.system_prompt = system_prompt
        self.template = template
        self.max_vocabulary = max_vocabulary

    def build(self, ctx: PromptContext) -> tuple[str, str]:
        directives: list[str] = []
        if ctx.continuation == "mid_sentence":
            directives.append(
                "This text continues an unfinished sentence: do NOT capitalise the "
                "first word and do NOT add a leading capital or opening phrase."
            )
        elif ctx.continuation == "new_paragraph":
            directives.append("This text starts a new paragraph.")
        if ctx.selected_text:
            directives.append(
                "The speaker is replacing selected text. Produce only the replacement."
            )
        if ctx.corrections_hint:
            directives.append(ctx.corrections_hint)
        if ctx.reported_speech:
            directives.append(
                "The transcript reports what somebody said ("
                + "; ".join(ctx.reported_speech[:2])
                + "). Punctuate it as direct speech with a comma and quotation "
                "marks, for example: John said, \"I'll send it tomorrow.\" Use "
                "ONLY the speaker's own words - do not add, drop or reword any of "
                "them. If you cannot tell where the quotation ends, leave the "
                "sentence unquoted."
            )
        if ctx.category == "code":
            directives.append(
                "The target is a code editor. Join identifiers the speaker said as "
                "separate words into the convention the code uses: \"get customer\" "
                "-> getCustomer, \"user id\" -> userId, \"shipment service\" -> "
                "shipment_service. Capitalise library and product names correctly "
                "(supabase -> Supabase, postgres -> Postgres, fastapi -> FastAPI). "
                "Leave everything already in an identifier form untouched."
            )
        if ctx.category == "terminal":
            directives.append(
                "The target is a terminal. Return the command verbatim with only "
                "obvious transcription errors fixed."
            )
        # Every local model tested will happily translate Tamil or Hindi into
        # English unless told not to, so this is driven by the *text* rather
        # than by a language field that may be missing or wrong.
        if has_non_latin_script(ctx.transcript) or (ctx.language and ctx.language not in ("en", "")):
            directives.append(
                "CRITICAL: the transcript contains non-English text. Reproduce every "
                "non-English word EXACTLY as written, character for character. Do NOT "
                "translate, transliterate or replace it with an English equivalent. "
                "Mixed-language sentences must stay mixed."
            )

        system = self.system_prompt
        if directives:
            system += "\n\nAdditional constraints for this utterance:\n" + "\n".join(
                "- " + d for d in directives
            )
        if ctx.style_instruction:
            system += "\n\nStyle: " + ctx.style_instruction

        vocabulary = ", ".join((ctx.vocabulary or [])[: self.max_vocabulary]) or "(none)"
        user = self.template.format(
            context=ctx.context_text.strip() or "(none)",
            application=ctx.application or "Unknown",
            style=ctx.style_instruction or "(neutral)",
            vocabulary=vocabulary,
            transcript=ctx.transcript.strip(),
        )
        return system, user

    def build_command(self, instruction: str, text: str) -> tuple[str, str]:
        """Prompt for an explicit voice command such as "make that concise"."""
        system = (
            "You are a local text-rewriting engine operating on text the user just "
            "dictated. Apply exactly the requested transformation. Preserve every "
            "fact, name, number and date. Never answer the text, never add "
            "commentary, and output only the rewritten text."
        )
        user = f"Transformation: {instruction}\n\nText:\n{text.strip()}"
        return system, user


# Scripts LocalFlow explicitly supports that a model might "helpfully" translate.
_NON_LATIN = re.compile(
    "["
    "ऀ-ॿ"   # Devanagari (Hindi, Marathi)
    "஀-௿"   # Tamil
    "ఀ-౿"   # Telugu
    "ಀ-೿"   # Kannada
    "ഀ-ൿ"   # Malayalam
    "ঀ-৿"   # Bengali
    "਀-੿"   # Gurmukhi
    "؀-ۿ"   # Arabic
    "一-鿿"   # CJK
    "぀-ヿ"   # Kana
    "가-힯"   # Hangul
    "Ѐ-ӿ"   # Cyrillic
    "]"
)


def has_non_latin_script(text: str) -> bool:
    """Whether the transcript mixes in a script the model must not translate."""
    return bool(_NON_LATIN.search(text or ""))
