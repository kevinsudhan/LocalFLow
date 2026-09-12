"""Comma-series lists: 'I need five things: a, b, c and d'.

The three older detectors all key off an ascending enumeration ("one ... two
... three"), which a dictated shopping list simply does not contain. This one
keys off commas instead, so it needs much stronger guards: commas are the most
common punctuation in ordinary prose and turning prose into bullets would be
inventing structure the speaker never dictated.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from localflow.processing.structure import detect_list  # noqa: E402


def bullets(text: str) -> list[str]:
    return [line[2:] for line in detect_list(text).splitlines() if line.startswith("- ")]


def test_the_dictation_that_prompted_this() -> None:
    spoken = (
        "let's check whether it's working. As of now, it seems to be working "
        "properly, and let's see if it can make a list. I need a list of five "
        "items I need to get: banana, apple, orange, crayon, and A4 sheets."
    )
    result = detect_list(spoken)
    assert bullets(spoken) == ["Banana", "Apple", "Orange", "Crayon", "A4 sheets"]
    # The prose before the list must survive intact, ending on the colon.
    assert result.split("\n")[0].endswith("I need a list of five items I need to get:")


def test_series_without_an_oxford_comma_still_splits() -> None:
    assert bullets("Here are the items: milk, eggs, bread and butter.") == [
        "Milk",
        "Eggs",
        "Bread",
        "Butter",
    ]


@pytest.mark.parametrize(
    "text",
    [
        # No list cue - just a sentence with commas.
        "I went to the shop, bought some milk, and came home.",
        # No conjunction closing the series: it trailed off.
        "I need a list of items: milk, eggs, bread,",
        # The speaker said three; there are four. The commas mean something else.
        "I need a list of three items: milk, eggs, bread and butter.",
        # Clauses, not items.
        "The following happened: we drove all the way to the coast, we ate a "
        "long lunch by the harbour, and we came back after dark.",
        # Two items is a pair, not a list.
        "Here are the items: milk and eggs.",
        # No colon introducing the series.
        "I need a list of five items I need to get banana, apple and orange.",
    ],
)
def test_prose_is_left_alone(text: str) -> None:
    assert detect_list(text) == text, "prose was turned into a list"


def test_stated_count_that_agrees_is_accepted() -> None:
    assert bullets("I need four things: rice, dal, onions and tomatoes.") == [
        "Rice",
        "Dal",
        "Onions",
        "Tomatoes",
    ]


def test_explicit_enumeration_still_wins() -> None:
    result = detect_list("One finish the CRM two fix Outlook three test ICEGATE")
    assert result.startswith("1. Finish the CRM")
    assert "- " not in result


def test_disabled_leaves_text_untouched() -> None:
    spoken = "Here are the items: milk, eggs, bread and butter."
    assert detect_list(spoken, enabled=False) == spoken
