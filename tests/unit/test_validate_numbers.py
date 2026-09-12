"""A spelled-out number is still a number.

This exists because of a rewrite that was accepted in production. The speaker
said "I have to buy five things, apple, water bottle, A4 sheets, a phone, and
chili powder"; the model returned only the five bullets, deleting the sentence
that introduced them. The validator compared digit runs, the source contained
no digits at all, so there was nothing to compare and the check passed
trivially. The count the speaker said was simply gone.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from localflow.processing.validate import validate_llm_output  # noqa: E402


def test_dropping_a_spoken_count_is_rejected() -> None:
    source = (
        "I have to buy five things, apple, water bottle, A4 sheets, a phone, "
        "and chili powder."
    )
    produced = "- Apple\n- Water bottle\n- A4 sheets\n- Phone\n- Chili powder"
    result = validate_llm_output(source, produced)
    assert not result.ok
    assert result.reason == "dropped_numbers"


@pytest.mark.parametrize(
    "produced",
    [
        # Kept as a word.
        "I have to buy five things: apple, water and phone.",
        # Rewritten as a digit, which is formatting rather than loss.
        "I have to buy 5 things: apple, water and phone.",
    ],
)
def test_a_surviving_count_is_accepted(produced: str) -> None:
    source = "I have to buy five things, apple, water and phone."
    assert validate_llm_output(source, produced).ok


def test_a_correction_still_tolerates_removal() -> None:
    """Superseded values are meant to disappear when the speaker corrects."""
    source = "Send it Monday, actually no, make that five o'clock Tuesday."
    assert validate_llm_output(source, "Send it at five o'clock on Tuesday.").ok


def test_ordinary_rewrites_are_unaffected() -> None:
    source = "uh can you send Rahul the quotation tomorrow"
    assert validate_llm_output(source, "Can you send Rahul the quotation tomorrow?").ok
