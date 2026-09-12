"""Spoken self-correction must delete what it supersedes - and nothing else."""
from __future__ import annotations

import pytest

from localflow.processing.backtrack import classify_chunk, resolve_corrections
from localflow.processing.tokens import tokenize

CORRECTIONS = [
    ("I'll send it Monday... actually Tuesday.", "I'll send it Tuesday."),
    ("Send it to Rahul... no, send it to Karthik.", "send it to Karthik."),
    ("The meeting is at 4... wait, make that 4:30.", "The meeting is at 4:30."),
    ("The shipment goes to Chennai—actually Bangalore.", "The shipment goes to Bangalore."),
    ("Let's meet at 3 pm, sorry, 5 pm.", "Let's meet at 5 pm."),
    ("Invite Priya, no wait, Anand.", "Invite Anand."),
    ("The total is 500, I meant 5000.", "The total is 5000."),
    ("Ship it on Thursday, or rather Friday.", "Ship it on Friday."),
    (
        "Can you send Rahul the quotation tomorrow, actually no, Friday, and tell him "
        "customs has delayed the shipment",
        "Can you send Rahul the quotation Friday, and tell him customs has delayed the shipment",
    ),
]

# Text that merely contains a cue word must survive untouched.
UNTOUCHED = [
    "Actually, I think we should go.",
    "I don't actually know the answer.",
    "There's no way to do that.",
    "Please wait for the confirmation email.",
    "I would rather use the other supplier.",
    "No problem, the invoice is attached.",
    "Sorry for the delay on the shipment.",
]


@pytest.mark.parametrize("spoken,expected", CORRECTIONS)
def test_corrections_resolve(spoken: str, expected: str) -> None:
    assert resolve_corrections(spoken).text.strip() == expected


@pytest.mark.parametrize("spoken", UNTOUCHED)
def test_ordinary_speech_is_untouched(spoken: str) -> None:
    assert resolve_corrections(spoken).text.strip() == spoken


def test_superseded_value_is_gone() -> None:
    result = resolve_corrections("I'll send it Monday... actually Tuesday.")
    assert "Monday" not in result.text
    assert result.events and result.events[0].removed == "Monday"


def test_unresolved_cue_is_reported_not_guessed() -> None:
    # No same-typed target exists, so the text must be left for the LLM stage.
    result = resolve_corrections("The plan is fine, actually let me think about it.")
    assert result.events == []


def test_scratch_that_drops_previous_sentence() -> None:
    result = resolve_corrections("The invoice is ready. Scratch that. The invoice is pending.")
    assert "ready" not in result.text


def test_disabled_is_a_no_op() -> None:
    text = "I'll send it Monday... actually Tuesday."
    assert resolve_corrections(text, enabled=False).text == text


@pytest.mark.parametrize(
    "chunk,kind",
    [
        ("Tuesday", "weekday"),
        ("4:30", "time"),
        ("5 pm", "time"),
        ("5000", "number"),
        ("Bangalore", "proper"),
        ("Friday and tell him customs", "generic"),
        ("send it to Karthik", "generic"),
    ],
)
def test_chunk_classification_is_strict(chunk: str, kind: str) -> None:
    tokens = [t for t in tokenize(chunk) if t.is_word or t.is_number]
    assert classify_chunk(tokens) == kind


def test_repeated_corrections_all_apply() -> None:
    result = resolve_corrections(
        "Ship it Monday, actually Tuesday. Send it to Rahul, no, to Karthik."
    )
    assert "Monday" not in result.text
    assert "Rahul" not in result.text
