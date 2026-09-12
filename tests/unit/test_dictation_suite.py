"""The dictation acceptance suite.

Realistic utterances, run through the deterministic pipeline exactly as a real
dictation would be. Cases marked ``needs_llm`` assert only what the
deterministic stage must guarantee on its own, plus that the stage *escalates*
rather than silently returning unfinished text - which is the failure mode that
matters, because a silent pass means the LLM never sees it either.
"""
from __future__ import annotations

import pytest

from localflow.context.engine import ContextEngine, ContextSnapshot
from localflow.processing.pipeline import PipelineInput, ProcessingPipeline
from localflow.snippets.engine import SnippetEngine
from localflow.vocabulary.engine import VocabularyEngine


@pytest.fixture(scope="module")
def pipeline() -> ProcessingPipeline:
    """The deterministic pipeline, with no LLM attached."""
    vocabulary = VocabularyEngine()
    vocabulary.load([{"term": "ICEGATE", "sounds_like": ["ice gate"]}], [])
    snippets = SnippetEngine()
    snippets.load([])
    context = ContextEngine()
    context.configure([], "neutral", True)
    return ProcessingPipeline(vocabulary, snippets, context, llm=None)


def run(pipeline: ProcessingPipeline, text: str, exe: str = "notepad.exe", **context):
    snapshot = ContextSnapshot.from_dict({"exe": exe, **context})
    resolved = pipeline.context_engine.resolve(snapshot, llm_globally_enabled=False)
    return pipeline.run(PipelineInput(raw_text=text, context=resolved))


# (id, spoken, must_contain, must_not_contain, must_match, escalates)
CASES = [
    (
        "filler-removal",
        "um I think we should probably move the meeting to tomorrow",
        ["I think we should probably move the meeting to tomorrow"],
        ["um "],
        [r"\.$"],
        False,
    ),
    (
        "number-word-correction",
        "I will meet you at five actually six no six thirty",
        [],
        [],
        [],
        True,   # no typed target; must reach the model rather than pass silently
    ),
    (
        "ordinal-list",
        "I need three things first call John second email Sarah third submit the report",
        ["I need three things:", "1. Call John", "2. Email Sarah", "3. Submit the report"],
        [],
        [],
        False,
    ),
    (
        "reported-speech",
        "John said I will send it tomorrow",
        ["John said", "send it tomorrow"],
        [],
        [],
        True,   # where the quotation ends is a judgement call
    ),
    (
        "explicit-quote",
        "quote I will send the documents by Friday end quote",
        ["I will send the documents by Friday"],
        ["quote", "end quote"],
        [r"^[“\"]", r"[”\"]$"],
        False,
    ),
    (
        "email-shape",
        "email Sarah saying hi Sarah just wanted to check if we are still on for "
        "tomorrow thanks Kevin",
        ["Hi Sarah,", "Just wanted to check if we are still on for tomorrow.", "Thanks,", "Kevin"],
        ["email Sarah saying"],
        [r"^Hi Sarah,\n\n"],
        False,
    ),
    (
        "developer-identifiers",
        "can you update the API endpoint to /api/v2/users",
        ["/api/v2/users", "API"],
        [],
        [r"\?$"],
        False,
    ),
    (
        "continuation",
        "if you are available tomorrow",
        ["if you are available tomorrow"],
        [],
        [r"^if"],       # must not capitalise mid-sentence
        False,
    ),
    (
        "tamil-preserved",
        "நாளைக்கு meeting இருக்கு",
        ["நாளைக்கு", "meeting", "இருக்கு"],
        [],
        [],
        False,
    ),
    (
        "spoken-parentheses",
        "open parenthesis user ID close parenthesis",
        ["(", ")", "ser ID"],
        ["open parenthesis", "close parenthesis"],
        [],
        False,
    ),
    (
        "ordinal-list-imperative",
        "first install Docker second clone the repo third run the application",
        ["1. Install Docker", "2. Clone the repo", "3. Run the application"],
        [],
        [],
        False,
    ),
    (
        "noun-phrase-correction",
        "I want to buy a record actually a present for my brother",
        [],
        [],
        [],
        True,   # "a record" -> "a present" needs semantics
    ),
    (
        "false-start",
        "um um I mean let us let us do it tomorrow",
        ["do it tomorrow"],
        ["um", "let us let us"],
        [],
        False,
    ),
    (
        "bullet-list",
        "bullet call the supplier bullet confirm the booking bullet send the invoice",
        ["- Call the supplier", "- Confirm the booking", "- Send the invoice"],
        ["bullet"],
        [],
        False,
    ),
]


@pytest.mark.parametrize(
    "case_id,spoken,must,must_not,patterns,escalates",
    CASES,
    ids=[c[0] for c in CASES],
)
def test_dictation(pipeline, case_id, spoken, must, must_not, patterns, escalates):
    import re

    context = {}
    if case_id == "continuation":
        context = {"text_before": "Hey Sarah, just wanted to ask ", "has_uia_text": True}
    exe = "outlook.exe" if case_id == "email-shape" else "notepad.exe"
    result = run(pipeline, spoken, exe=exe, **context)
    output = result.text

    for needle in must:
        assert needle in output, f"{case_id}: missing {needle!r} in {output!r}"
    for needle in must_not:
        assert needle.lower() not in output.lower(), (
            f"{case_id}: should not contain {needle!r} in {output!r}"
        )
    for pattern in patterns:
        assert re.search(pattern, output), f"{case_id}: /{pattern}/ did not match {output!r}"

    if escalates:
        reason = result.llm_reason or result.llm_skipped_reason
        assert reason not in ("deterministic_sufficient", ""), (
            f"{case_id}: silently returned unprocessed text instead of escalating "
            f"(reason={reason!r}, output={output!r})"
        )


def test_no_case_is_left_unchanged_without_a_reason(pipeline):
    """Anything the deterministic stage cannot finish must say so."""
    for case_id, spoken, _must, _must_not, _patterns, escalates in CASES:
        result = run(pipeline, spoken)
        unchanged = result.text.strip().lower() == spoken.strip().lower()
        if unchanged and not escalates:
            pytest.fail(f"{case_id} passed through untouched and did not escalate")
