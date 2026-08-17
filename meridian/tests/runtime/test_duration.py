"""ISO 8601 durations, the only way the spec talks about time."""

import re
from datetime import timedelta

import pytest

from meridian.domain.primitives import ISO_8601_DURATION
from meridian.runtime.duration import DurationError, parse


def test_the_deadline_on_the_seed_board():
    assert parse("PT48H") == timedelta(hours=48)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("PT30S", timedelta(seconds=30)),
        ("PT5M", timedelta(minutes=5)),
        ("P5D", timedelta(days=5)),
        ("P2W", timedelta(weeks=2)),
        ("P1DT2H30M", timedelta(days=1, hours=2, minutes=30)),
    ],
)
def test_the_designators_a_process_owner_reaches_for(text: str, expected: timedelta):
    assert parse(text) == expected


def test_no_deadline_is_not_a_deadline_of_zero():
    # The distinction is the whole reason this returns `None` rather than
    # `timedelta(0)`. An absent deadline means the process owner set no time
    # limit and the caller waits indefinitely; zero would expire immediately.
    assert parse(None) is None


def test_a_duration_this_runtime_cannot_represent_is_refused():
    # Years and months have no fixed length, so a timedelta would be a lie.
    # Failing loudly beats silently treating a year as 365 days.
    with pytest.raises(DurationError):
        parse("P1Y")
    with pytest.raises(DurationError):
        parse("P3M")


def test_garbage_is_refused():
    for text in ("48h", "", "P", "PT", "T48H"):
        with pytest.raises(DurationError):
            parse(text)


def test_the_month_designator_is_rejected_here_but_legal_on_a_card():
    # Worth knowing rather than assuming: the domain pattern accepts P1M, so a
    # process owner can write one and only the runtime refuses it. That is a
    # real gap between what a board may say and what an agent can execute.
    assert re.match(ISO_8601_DURATION, "P1M")
    with pytest.raises(DurationError):
        parse("P1M")
