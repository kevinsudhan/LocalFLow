"""Both shapes a dictated list arrives in.

Whether a colon appears at all depends on punctuation the speaker never said -
it comes from Whisper, or from the cleanup model, or not at all. These are the
same utterance and must produce the same list:

    I need five things: apple, banana and mango.
    I need five things. It will be apple, banana and mango.

The second form is what an actual dictation produced, and it was silently left
as prose because the detector required the colon.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from localflow.processing.structure import detect_list  # noqa: E402

EXPECTED = ["Apple", "Banana", "Orange", "Mango", "A4 sheets"]


def bullets(text: str) -> list[str]:
    return [line[2:] for line in detect_list(text).splitlines() if line.startswith("- ")]


@pytest.mark.parametrize(
    "spoken",
    [
        "Ok, I need to buy 5 things. It will be Apple, Banana, Orange, Mango and A4 sheets.",
        "Ok, I need to buy 5 things. It'll be Apple, Banana, Orange, Mango and A4 sheets.",
        "Ok, I need to buy 5 things. They are Apple, Banana, Orange, Mango and A4 sheets.",
        "Ok, I need to buy 5 things: Apple, Banana, Orange, Mango and A4 sheets.",
        "Ok, I need to buy 5 things. Apple, Banana, Orange, Mango and A4 sheets.",
    ],
)
def test_every_shape_produces_the_same_list(spoken: str) -> None:
    assert bullets(spoken) == EXPECTED


def test_the_announcement_keeps_its_prose_and_gains_a_colon() -> None:
    spoken = "Ok, I need to buy 5 things. It will be Apple, Banana, Orange, Mango and A4 sheets."
    assert detect_list(spoken).split("\n")[0] == "Ok, I need to buy 5 things:"


def test_earlier_sentences_are_preserved() -> None:
    spoken = (
        "Hey guys, what are you guys doing? I need to buy 5 things as a list. "
        "It'll be Apple, Banana, Orange, Mango and A4 sheets."
    )
    first = detect_list(spoken).split("\n")[0]
    assert first.startswith("Hey guys, what are you guys doing?")
    assert first.endswith("I need to buy 5 things as a list:")


@pytest.mark.parametrize(
    "text",
    [
        # "buy" alone is not an announcement - no list noun, no count.
        "I need to buy a car. It was red, shiny, fast and expensive.",
        # Stated count disagrees with the number of items.
        "I need 3 things. It will be milk, eggs, bread and butter.",
        # No announcement anywhere.
        "I went to the shop, bought some milk, and came home.",
    ],
)
def test_prose_is_still_left_alone(text: str) -> None:
    assert detect_list(text) == text, "prose was turned into a list"


def test_connector_inside_one_sentence() -> None:
    """'five things which are A, B and C' - no colon, one sentence.

    Whisper punctuates this as a single sentence, so the announcement and the
    series arrive joined by a relative clause rather than a colon. This is the
    form an actual dictation produced.
    """
    spoken = "I need to buy 5 things which are an apple, banana, crayons, A4 sheets and a clip."
    assert bullets(spoken) == ["An apple", "Banana", "Crayons", "A4 sheets", "A clip"]
    assert detect_list(spoken).split("\n")[0] == "I need to buy 5 things:"


@pytest.mark.parametrize(
    "spoken,expected",
    [
        (
            "I have three errands namely the bank, the post office and the chemist.",
            ["The bank", "The post office", "The chemist"],
        ),
        (
            "The steps are as follows: unplug it, wait ten seconds and plug it back in.",
            ["Unplug it", "Wait ten seconds", "Plug it back in"],
        ),
    ],
)
def test_other_connectors(spoken: str, expected: list[str]) -> None:
    assert bullets(spoken) == expected


def test_a_relative_clause_is_not_a_list() -> None:
    """'which is' after a noun with no list announcement is just a clause."""
    prose = "I bought a car which is red, shiny and fast."
    assert detect_list(prose) == prose


def test_a_stuttered_last_word_does_not_hide_the_list() -> None:
    """Whisper repeats the final word: '... chilli and guava. Guava.'

    The fragment is not a sentence, but it sits where the series should be, so
    the "series must close the utterance" rule gave up before finding the list.
    """
    spoken = "I have to buy 5 things. Carrot, onion, tomato, chilli and guava. Guava."
    assert bullets(spoken) == ["Carrot", "Onion", "Tomato", "Chilli", "Guava"]


def test_a_real_trailing_sentence_still_blocks_the_list() -> None:
    """Only a repeat of what came before is treated as a stutter."""
    prose = "I need 3 things: milk, eggs and bread. Thanks very much everyone."
    assert detect_list(prose) == prose


def test_any_counted_plural_noun_announces_a_list() -> None:
    """'five AIs' counts, not just a hard-coded vocabulary of list nouns.

    Restricting the count to items/things/steps meant an ordinary sentence like
    "the five AIs that I know are ..." was invisible to the detector.
    """
    spoken = "the five AIs that I know are Claude, Gemini, Grok, DeepSeek, and Kimi."
    assert bullets(spoken) == ["Claude", "Gemini", "Grok", "DeepSeek", "Kimi"]


def test_the_relative_clause_survives_in_the_heading() -> None:
    """Only the copula is consumed - the heading must still say what was said."""
    spoken = "the five AIs that I know are Claude, Gemini, Grok, DeepSeek, and Kimi."
    assert detect_list(spoken).split("\n")[0] == "the five AIs that I know:"

    # A dangling "which" left by the split is trimmed, though.
    spoken = "I need 5 things which are an apple, banana, crayons, A4 sheets and a clip."
    assert detect_list(spoken).split("\n")[0] == "I need 5 things:"


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("The three colours are red, blue and green.", ["Red", "Blue", "Green"]),
        (
            "The five AIs I know are Claude, Gemini, Grok, DeepSeek and Kimi.",
            ["Claude", "Gemini", "Grok", "DeepSeek", "Kimi"],
        ),
    ],
)
def test_bare_copula_with_a_stated_count(spoken: str, expected: list[str]) -> None:
    assert bullets(spoken) == expected


@pytest.mark.parametrize(
    "text",
    [
        # A measurement, not an enumeration.
        "I waited five minutes, made tea and went back to work.",
        # Count disagrees: two announced, three listed.
        "The two options are red, blue and green.",
        # No announcement at all.
        "I bought a car which is red, shiny and fast.",
        # A count, but what follows is a clause rather than a series.
        "There are 3 reasons why this is hard and I will explain them.",
    ],
)
def test_generalised_count_does_not_bulletise_prose(text: str) -> None:
    assert detect_list(text) == text, "prose was turned into a list"


def test_a_bare_comma_can_separate_announcement_from_series() -> None:
    """'5 things, carrot, tomato and onion' - Whisper writes a comma for a pause.

    A comma is the weakest separator there is, so this path demands the
    strongest evidence: a generic list noun and a count that matches exactly.
    """
    spoken = "I want to buy 5 things, Carrot, Tomato, Onion, Chilli Powder and A4 sheets."
    assert bullets(spoken) == ["Carrot", "Tomato", "Onion", "Chilli Powder", "A4 sheets"]
    assert detect_list(spoken).split("\n")[0] == "I want to buy 5 things:"


@pytest.mark.parametrize(
    "text",
    [
        # A specific counted noun, not a generic list noun: these may well be
        # four separate purchases rather than a list of three.
        "I bought 3 books, a pen, a ruler and a bag.",
        # No count to corroborate the comma.
        "I want to buy things, carrot, tomato and onion.",
        # Count disagrees with the number of items.
        "I want to buy 5 things, carrot, tomato and onion.",
    ],
)
def test_the_comma_path_demands_corroboration(text: str) -> None:
    assert detect_list(text) == text, "prose was turned into a list"


@pytest.mark.parametrize(
    "spoken,expected",
    [
        (
            "I need to buy things such as crayons, onions, tomatoes and curd packet.",
            ["Crayons", "Onions", "Tomatoes", "Curd packet"],
        ),
        (
            "Bring a few items including a pen, a ruler and some paper.",
            ["A pen", "A ruler", "Some paper"],
        ),
    ],
)
def test_examples_connector_with_enumeration_intent(spoken: str, expected: list[str]) -> None:
    """'such as' counts only when the speaker is listing things to act on."""
    assert bullets(spoken) == expected


@pytest.mark.parametrize(
    "text",
    [
        # "such as" introduces examples; this is a sentence, not four bullets.
        "I like things such as walking, reading and coffee.",
        "We discussed things such as budget, timelines and staffing.",
    ],
)
def test_examples_connector_without_intent_stays_prose(text: str) -> None:
    assert detect_list(text) == text, "prose was turned into a list"
