"""A board key from something a person typed.

Twenty lines of string work that reach further than they look. A key becomes a
generated Python filename, a spec entry, and the thing every thread anchor,
assertion and eval failure points at — with **no foreign key**, deliberately, so
that a card can be deleted and re-created during review without orphaning its
conversation. It is also the one value this package writes into a column with a
domain check on it (`board_key`, `^[a-z][a-z0-9_]{1,63}$`), so a key that does
not match is not a bad name; it is a failed insert.

Two properties carry the file. **Whatever comes out matches the pattern**, for
any input at all — which is why the last test fuzzes rather than enumerates. And
**a key is a word**, never `p_7`: somebody reads it in a stack trace.
"""

import re

import pytest

from meridian.domain.keys import key_for
from meridian.domain.primitives import BOARD_KEY


def test_a_key_is_made_from_the_words_someone_typed() -> None:
    assert key_for("COAs valid?", taken=(), fallback="check") == "coas_valid"


def test_punctuation_and_spacing_collapse_to_single_underscores() -> None:
    # Not `report___coa`. Runs collapse, edges are trimmed.
    assert key_for("  Report — the COA   discrepancy!  ", taken=(), fallback="action") == (
        "report_the_coa_discrepancy"
    )


def test_a_key_always_starts_with_a_letter() -> None:
    # The pattern demands it, and a leading digit would be a rejected insert
    # rather than an ugly name.
    assert key_for("2026 review", taken=(), fallback="check") == "review"


def test_a_name_with_nothing_usable_falls_back_to_the_card_type() -> None:
    # A key reaches generated code and stack traces. "check" tells a reader
    # something; "p_7" tells them to go and look it up.
    assert key_for("???", taken=(), fallback="check") == "check"
    assert key_for("发票", taken=(), fallback="entity") == "entity"
    assert key_for("", taken=(), fallback="event") == "event"


def test_accents_survive_as_the_letters_they_are() -> None:
    # `r_ckgabe` would be the lazy answer and it loses a letter of a real word.
    assert key_for("Rückgabe", taken=(), fallback="action") == "ruckgabe"


def test_a_single_character_name_still_clears_the_two_character_floor() -> None:
    # `board_key` is `[a-z][a-z0-9_]{1,63}` — two characters minimum, so a
    # one-letter name is *shorter* than the pattern allows.
    assert re.match(BOARD_KEY, key_for("A", taken=(), fallback="check"))


# --- collisions --------------------------------------------------------------


def test_the_second_card_of_a_name_is_numbered_from_two() -> None:
    # Never `_1`: the first one is unsuffixed, so `_1` would imply it was
    # numbered too and that there is a card somewhere called `coas_valid_1`.
    assert key_for("COAs valid", taken=["coas_valid"], fallback="check") == "coas_valid_2"


def test_numbering_keeps_going_past_the_second() -> None:
    taken = ["coas_valid", "coas_valid_2", "coas_valid_3"]
    assert key_for("COAs valid", taken=taken, fallback="check") == "coas_valid_4"


def test_a_fallback_collides_and_numbers_like_anything_else() -> None:
    assert key_for("???", taken=["check"], fallback="check") == "check_2"


# --- the ceiling -------------------------------------------------------------


def test_a_long_name_is_cut_to_something_that_still_fits() -> None:
    key = key_for(
        "Does every line item on the commercial invoice carry all four of the required identifiers",
        taken=(),
        fallback="check",
    )

    assert len(key) <= 64
    assert re.match(BOARD_KEY, key)
    # Cut at a word boundary rather than mid-word, so it is still readable.
    assert not key.endswith("_")


def test_a_long_name_that_collides_still_fits() -> None:
    # The suffix has to fit *inside* the ceiling, not be appended past it — this
    # is the case a truncate-then-suffix implementation gets wrong.
    long_name = "x" * 90
    first = key_for(long_name, taken=(), fallback="check")
    second = key_for(long_name, taken=[first], fallback="check")

    assert len(second) <= 64
    assert second != first
    assert re.match(BOARD_KEY, second)


# --- the property that has to hold for anything -------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "COAs valid?",
        "Report the discrepancy",
        "2026 review",
        "???",
        "",
        "   ",
        "Rückgabe / Erstattung",
        "a",
        "_leading underscore",
        "trailing underscore_",
        "x" * 200,
        "Pre-Alert Documents // APL USA",
        "100% complete",
        "e1",
        "__",
        "发票 processing",
        "naïve café — résumé",
    ],
)
def test_whatever_comes_out_is_a_legal_key(name: str) -> None:
    # The one function in this package that writes into a column with a domain
    # check on it. A name that produces an illegal key is a failed insert, so
    # this is asserted over inputs rather than reasoned about per case.
    assert re.match(BOARD_KEY, key_for(name, taken=(), fallback="check"))


@pytest.mark.parametrize("name", ["COAs valid", "???", "x" * 200, "2026"])
def test_a_key_is_never_one_that_is_already_taken(name: str) -> None:
    taken: list[str] = []
    for _ in range(5):
        key = key_for(name, taken=taken, fallback="check")
        assert key not in taken
        assert re.match(BOARD_KEY, key)
        taken.append(key)
