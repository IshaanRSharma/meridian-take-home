"""What the process owner is asked, in their words.

The mirror of `test_prompts.py`. That file guards one direction — the demo
customer's nouns must not reach a prompt, or the model is being told what to ask
rather than how. This guards the other: **our field names must not reach the
person answering.**

A lint-raised question used to end with *"and `idempotency_key` is where it would
go"*, which names a schema field to somebody who has never seen the schema and
never will. It is the same mistake as a `channel: system_of_record` enum, one
layer up, and it was caught by a person reading the screen rather than by
anything here.

Where the answer lands is ours to know. `Thread.decision_key` already carries
`(anchor, field)` for dedup and for the audit path, so the field is known
without being said out loud — which is the whole reason this can be a test
rather than a trade-off.

Board keys are the exception, and they are not an exception at all: an outcome
called `mismatched_coa` is the owner's own word, quoted back. So this walks the
config field names rather than looking for underscores, and the two never
collide.
"""

from meridian.compiler import rules
from meridian.domain.graph import Board
from meridian.domain.primitives import (
    ActionConfig,
    CheckConfig,
    EntityConfig,
    EventConfig,
)
from meridian.reviewer.run import _from_blanks

# Only the compound ones. A single-word field like `channel` or `system` is also
# an ordinary English word, and `Finding.reason` uses several deliberately —
# "This has a channel but reaches nobody" is a sentence, not a field reference.
# Every name here contains an underscore, which no process owner ever types, so
# a hit is unambiguous.
OURS = sorted(
    {
        field
        for config in (EntityConfig, EventConfig, ActionConfig, CheckConfig)
        for field in config.model_fields
        if "_" in field
    }
)


def named_in(text: str) -> list[str]:
    return [field for field in OURS if field in text]


def test_the_guard_has_something_to_guard() -> None:
    # The one way this fails silently: match nothing, pass, protect nothing.
    assert {"correlation_key", "idempotency_key", "on_missing_input"} <= set(OURS)


def test_no_finding_a_card_reports_about_itself_names_a_field(seed: Board) -> None:
    # `Finding.reason` is UI copy that happens to live in the domain model — it
    # is the text shown on the card, and `_from_blanks` copies it verbatim into
    # the question. Guarding it here guards both surfaces from one place.
    leaked = [(f.anchor, f.reason, named_in(f.reason)) for f in rules.findings(seed)]

    assert not [row for row in leaked if row[2]]


def test_no_question_the_reviewer_raises_from_a_blank_names_a_field(seed: Board) -> None:
    # The half a person actually reads in the thread panel. `reason` is the one
    # that regressed: it explained where the answer would be stored, which is
    # our business and not theirs.
    asked = _from_blanks(seed, 1)

    assert asked, "the seed board has blanks; a round that asks nothing is the bug"
    assert not [t for t in asked if named_in(t.question) or named_in(t.reason or "")]


def test_the_field_is_still_known_even_though_it_is_not_said(seed: Board) -> None:
    # The reason this is a test and not a trade-off. Dedup across rounds and the
    # audit path both key on the field; dropping it from the sentence must not
    # drop it from the record.
    for thread in _from_blanks(seed, 1):
        assert thread.decision_key
        assert thread.decision_key.startswith("lint:")
