"""The transcript processing pipeline.

RAW ASR -> normalise -> vocabulary -> self-correction -> fillers -> structure
        -> punctuation -> (LLM only if needed) -> validate -> final text

The ordering is the spec's.  The important design decision is the *gate*: the
deterministic stages run always and are usually enough, and the local model is
invoked only when a specific signal says reasoning is required.  That is what
keeps a typical dictation at deterministic latency instead of paying for a
model round-trip every single time.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..commands import registry as commands
from ..config import LlmSettings, ProcessingSettings
from ..context.engine import ContextEngine, ResolvedContext
from ..llm.prompt import PromptBuilder, PromptContext
from ..llm.provider import LlmError, LocalLLMProvider, clean_model_output
from ..snippets.engine import SnippetEngine, SnippetMatch
from ..vocabulary.engine import VocabularyEngine
from .normalize import is_effectively_empty, normalize as normalize_text
from . import punctuation as punctuation_stage
from . import structure as structure_stage
from . import protect
from . import email as email_stage
from . import quotes as quotes_stage
from . import style as style_module
from .backtrack import resolve_corrections
from .fillers import filler_density, remove_fillers
from .tokens import split_sentences, word_count
from .validate import ValidationResult, sanity_check_final, validate_llm_output

log = logging.getLogger(__name__)

MIN_WORDS_FOR_LLM = 3
RUN_ON_WORDS = 32


@dataclass
class PipelineInput:
    raw_text: str
    context: ResolvedContext
    segments: list[dict] = field(default_factory=list)
    language: str = ""
    avg_logprob: float = 0.0
    duration_s: float = 0.0
    style_override: str = ""


@dataclass
class PipelineResult:
    text: str
    deterministic_text: str = ""
    raw_text: str = ""
    used_llm: bool = False
    llm_model: str = ""
    llm_reason: str = ""
    llm_skipped_reason: str = ""
    command: commands.Command | None = None
    snippet: SnippetMatch | None = None
    validation: ValidationResult | None = None
    corrections: list[dict] = field(default_factory=list)
    removed_fillers: list[str] = field(default_factory=list)
    vocabulary_hits: list[str] = field(default_factory=list)
    quotes_applied: list[str] = field(default_factory=list)
    email_formatted: bool = False
    timings: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    leading_space: bool = False

    @property
    def insert_text(self) -> str:
        return (" " if self.leading_space else "") + self.text


class ProcessingPipeline:
    def __init__(
        self,
        vocabulary: VocabularyEngine,
        snippets: SnippetEngine,
        context_engine: ContextEngine,
        llm: LocalLLMProvider | None = None,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self.vocabulary = vocabulary
        self.snippets = snippets
        self.context_engine = context_engine
        self.llm = llm
        self.prompts = prompt_builder or PromptBuilder()
        self.processing = ProcessingSettings()
        self.llm_settings = LlmSettings()

    def configure(self, processing: ProcessingSettings, llm: LlmSettings) -> None:
        self.processing = processing
        self.llm_settings = llm
        self.vocabulary.threshold = processing.vocabulary_fuzzy_threshold

    # -- main entry point --------------------------------------------------
    def run(self, data: PipelineInput) -> PipelineResult:
        timings: dict[str, float] = {}
        started = time.perf_counter()
        result = PipelineResult(text="", raw_text=data.raw_text)

        def mark(stage: str, since: float) -> float:
            now = time.perf_counter()
            timings[stage] = round((now - since) * 1000, 2)
            return now

        cursor = started
        text = normalize_text(
            data.raw_text, spoken_punctuation=self.processing.auto_punctuation
        )
        # URLs, paths and identifiers are swapped out before the prose stages
        # and restored before the LLM sees the text.
        text, protected_literals = protect.mask(text)
        cursor = mark("normalize", cursor)

        if is_effectively_empty(text):
            result.timings = timings
            result.text = ""
            return result

        # 1. Explicit voice commands and snippets bypass rewriting entirely.
        command = commands.detect(text, self.processing.commands_enabled)
        if command is not None:
            result.command = command
            result.text = ""
            result.deterministic_text = protect.unmask(text, protected_literals)
            result.timings = timings
            return result

        snippet = self.snippets.match(text, self.processing.snippets_enabled)
        if snippet is not None and snippet.mode == "replace_all":
            result.snippet = snippet
            result.text = snippet.expansion
            result.deterministic_text = snippet.expansion
            result.timings = timings
            return result
        cursor = mark("commands", cursor)

        # 2. Vocabulary and learned corrections.
        vocab = self.vocabulary.apply(text, self.processing.vocabulary_enabled)
        text = vocab.text
        result.vocabulary_hits = [pair[1] for pair in vocab.applied]
        cursor = mark("vocabulary", cursor)

        # 3. Spoken self-correction.
        backtrack = resolve_corrections(text, self.processing.resolve_corrections)
        text = backtrack.text
        result.corrections = [
            {"kind": e.kind, "cue": e.cue, "removed": e.removed, "replacement": e.replacement}
            for e in backtrack.events
        ]
        cursor = mark("corrections", cursor)

        # 4. Fillers.
        protected = set(self.vocabulary.terms) | set(data.context.proper_nouns)
        fillers = remove_fillers(
            text,
            enabled=self.processing.remove_fillers,
            aggressiveness=self.processing.filler_aggressiveness,
            protected=protected,
        )
        text = fillers.text
        result.removed_fillers = fillers.removed
        cursor = mark("fillers", cursor)

        # 5. Structure and punctuation.
        is_terminal = data.context.category == "terminal"
        if not is_terminal:
            quoted = quotes_stage.apply(text, self.processing.auto_quotes)
            text = quoted.text
            result.quotes_applied = quoted.applied
            reported_speech = quoted.reported_speech

            text = structure_stage.detect_list(text, self.processing.auto_lists)
            text = punctuation_stage.apply(
                text,
                auto_punctuation=self.processing.auto_punctuation,
                smart_capitalization=self.processing.smart_capitalization,
                continue_sentence=data.context.continues_sentence,
            )
            text = structure_stage.paragraphs_from_segments(
                text, data.segments, enabled=self.processing.auto_paragraphs
            )
            # Last, because it works on finished sentences.
            email = email_stage.format_email(
                text,
                enabled=self.processing.auto_email_format,
                is_email_app=data.context.category == "email",
            )
            text = email.text
            result.email_formatted = email.applied
        else:
            reported_speech = []
        cursor = mark("formatting", cursor)

        text = protect.unmask(text, protected_literals)
        deterministic = text.strip()
        result.deterministic_text = deterministic

        # 6. The LLM gate.
        use_llm, reason = self.should_use_llm(
            deterministic,
            data,
            unresolved=backtrack.unresolved_cues,
            ambiguous_fillers=fillers.ambiguous,
            reported_speech=reported_speech,
        )
        final = deterministic
        if use_llm and self.llm is not None:
            llm_started = time.perf_counter()
            refined, validation, model = self._refine(
                deterministic,
                data,
                backtrack.unresolved_cues,
                protected_literals,
                reported_speech,
            )
            timings["llm"] = round((time.perf_counter() - llm_started) * 1000, 2)
            result.validation = validation
            result.llm_model = model
            result.llm_reason = reason
            if refined:
                final = refined
                result.used_llm = True
            elif validation is not None and not validation.ok:
                result.warnings.append(f"Local model output rejected ({validation.reason}).")
        else:
            result.llm_skipped_reason = reason

        # 7. Re-apply the continuation rules the model may have undone.
        if not is_terminal and data.context.continues_sentence:
            final = punctuation_stage.strip_leading_terminator(final)
            final = punctuation_stage.lowercase_continuation(final)

        # 7b. Let the list detector see what the model produced.
        #
        # The model restructures prose - it turns "five things which are A, B
        # and C" into "five things: A, B and C" - and that happens long after
        # the deterministic list pass has run. Without a second look, a list the
        # model itself just made explicit is flattened back into a sentence, and
        # the user sees their list refuse to format for no visible reason.
        if result.used_llm and self.processing.auto_lists:
            final = structure_stage.detect_list(final, True)

        ok, message = sanity_check_final(final)
        if not ok:
            log.warning("Final sanity check failed: %s", message)
            result.warnings.append(message)
            final = deterministic if deterministic else ""
            ok2, _ = sanity_check_final(final)
            if not ok2:
                final = ""

        result.text = final.strip()
        result.leading_space = self._needs_leading_space(data.context, result.text)
        timings["total"] = round((time.perf_counter() - started) * 1000, 2)
        result.timings = timings
        return result

    # -- the gate ----------------------------------------------------------
    def should_use_llm(
        self,
        text: str,
        data: PipelineInput,
        unresolved: list[str] | None = None,
        ambiguous_fillers: list[str] | None = None,
        reported_speech: list[str] | None = None,
    ) -> tuple[bool, str]:
        """Deterministic-first: only escalate when there is a reason to."""
        if not self.llm_settings.enabled:
            return False, "llm_disabled"
        if self.llm is None:
            return False, "no_provider"
        if not data.context.llm_enabled:
            return False, "disabled_for_app"
        if data.context.category == "terminal":
            return False, "terminal_verbatim"
        if data.context.snapshot.is_password:
            return False, "password_field"
        if len(text) > self.llm_settings.max_input_chars:
            return False, "too_long"

        words = word_count(text)
        if words < MIN_WORDS_FOR_LLM:
            return False, "too_short"
        if self.llm_settings.always_use:
            return True, "always_on"

        if unresolved:
            return True, "unresolved_correction"
        if reported_speech:
            # Only the model can judge where the quotation ends.
            return True, "reported_speech"
        if ambiguous_fillers:
            return True, "ambiguous_fillers"
        if filler_density(text) > 0.05:
            return True, "filler_density"

        style = style_module.get(data.style_override or data.context.style)
        if style.key != style_module.NEUTRAL:
            return True, "style_" + style.key

        if data.avg_logprob and data.avg_logprob < -0.75:
            return True, "low_asr_confidence"

        for sentence in split_sentences(text):
            if word_count(sentence) >= RUN_ON_WORDS and sentence.count(",") <= 1:
                return True, "run_on_sentence"

        if _has_disfluent_restart(text):
            return True, "disfluent_restart"
        if data.context.category == "email" and words >= 12:
            return True, "email_formatting"
        if data.context.continues_sentence and words >= 8:
            return True, "continuation_fit"

        return False, "deterministic_sufficient"

    # -- LLM refinement ----------------------------------------------------
    def _refine(
        self,
        text: str,
        data: PipelineInput,
        unresolved: list[str],
        literals: list[str] | None = None,
        reported_speech: list[str] | None = None,
    ) -> tuple[str, ValidationResult | None, str]:
        literals = literals or []
        assert self.llm is not None
        model = self.llm_settings.model
        if not model:
            return "", None, ""

        style_key = data.style_override or data.context.style
        profile = style_module.get(style_key)
        hint = ""
        if unresolved:
            hint = (
                "The speaker corrected themselves near '"
                + "', '".join(unresolved[:3])
                + "'. DELETE the superseded wording entirely and keep only what "
                "replaced it. Do not merely add commas around the correction. "
                'Example: "buy a record actually a present" becomes '
                '"buy a present", not "buy a record, actually, a present".'
            )

        prompt_ctx = PromptContext(
            transcript=text,
            application=data.context.identity.app_name,
            category=data.context.category,
            style_instruction=style_module.instruction_for(style_key, data.context.category),
            context_text=(
                ContextEngine.context_for_prompt(data.context)
                if self.processing.use_context
                else ""
            ),
            vocabulary=self.vocabulary.prompt_terms(limit=40),
            corrections_hint=hint,
            continuation=data.context.continuation,
            reported_speech=list(reported_speech or []),
            language=data.language,
            selected_text=data.context.snapshot.selected_text,
        )
        system, user = self.prompts.build(prompt_ctx)

        try:
            response = self.llm.generate(
                system,
                user,
                model=model,
                timeout=self.llm_settings.timeout_s,
                keep_alive=self.llm_settings.keep_alive,
                num_ctx=self.llm_settings.num_ctx,
                temperature=self.llm_settings.temperature,
                top_p=self.llm_settings.top_p,
                num_predict=max(64, int(len(text) / 2) + 96),
            )
        except LlmError as exc:
            log.info("LLM refinement skipped: %s", exc)
            return "", ValidationResult(False, exc.code, str(exc)), model

        produced = clean_model_output(response.text)
        validation = validate_llm_output(
            text,
            produced,
            max_ratio=self.llm_settings.max_output_ratio,
            protected_terms=self.vocabulary.terms + protect.protected_terms(literals),
            allow_removals=bool(unresolved),
            style_allows_reordering=profile.allow_reordering,
        )
        if not validation.ok:
            log.info("Rejected LLM output (%s): %s", validation.reason, produced[:160])
            return "", validation, model
        return produced, validation, model

    def run_command(self, instruction: str, text: str) -> str:
        """Execute a restyle/translate voice command on already-inserted text."""
        if self.llm is None or not self.llm_settings.model:
            raise LlmError("llm_no_model", "No local model is selected for this command.")
        system, user = self.prompts.build_command(instruction, text)
        response = self.llm.generate(
            system,
            user,
            model=self.llm_settings.model,
            timeout=max(self.llm_settings.timeout_s, 20.0),
            keep_alive=self.llm_settings.keep_alive,
            num_ctx=self.llm_settings.num_ctx,
            temperature=0.1,
            num_predict=max(96, len(text)),
        )
        produced = clean_model_output(response.text)
        ok, message = sanity_check_final(produced)
        if not ok:
            raise LlmError("llm_failed", message)
        return produced

    @staticmethod
    def _needs_leading_space(ctx: ResolvedContext, text: str) -> bool:
        """Whether to prepend a space so the insertion does not glue onto a word."""
        if not text or ctx.replace_selection:
            return False
        before = ctx.snapshot.text_before
        if not ctx.snapshot.has_uia_text or not before:
            return False
        if before.endswith((" ", "\t", "\n", "(", "[", "{", "\"", "'", "-", "/")):
            return False
        if text[0] in ",.;:!?)]}":
            return False
        return bool(before[-1].isalnum() or before[-1] in ",.;:!?")


# A bare repeated word is NOT a signal: normalisation already collapsed the
# function-word stutters, so anything still doubled here ("very very good",
# "had had enough") is deliberate emphasis the model must not touch.
_RESTART = re.compile(
    r"\b(?:i|we|you|he|she|they|it)\s+(?:i|we|you|he|she|they|it)\b"
    r"|\b(?:the|a|an|to|of|and)\s+(?:the|a|an|to|of|and)\b"
    r"|\b(?:i|we)\s+(?:was|were|am|are)\b\s*[-–—,]\s*\b(?:i|we)\b",
    re.IGNORECASE,
)


def _has_disfluent_restart(text: str) -> bool:
    """False starts such as 'I was - I mean we were', or 'the the' survivors."""
    return bool(_RESTART.search(text))
