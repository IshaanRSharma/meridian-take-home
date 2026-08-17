"""Does a sentence actually land on the right fields, with a real model.

The rest of `tests/authoring/` runs against a fake transport, so it proves the
plumbing — that a draft is reassembled, that a blank is left alone, that a bad
value degrades to a question. None of it proves the thing the feature is for:
that *what somebody typed* reaches the field it belongs in. Only a real call can
say that, so this is marked and skipped by default, the same way the reviewer's
recall number is.

**Values, not keys.** Every expectation names what the field must contain,
because a field set to the wrong thing is the failure worth catching — and it is
invisible to a test that only asks whether the key is present. Two of these were
found exactly that way: *"if they've not come back in two days"* landing in
`timeout` (a clock on a system) instead of `timing.deadline` (a clock on a
person), which compiles to a timer nobody answers; and *"one of them per
shipment"* reading as `one_per` when the shipment **is** the case.

What is deliberately not asserted: `name` and `match_condition` are checked for
presence only. They are the model's own words for a label and a filter, and
pinning their wording would fail on a synonym while catching nothing.

The counterpart to all of this is `test_interpret.py::the model is never offered
a field it may not set` — together they are the two halves of the claim: the
schema decides what *may* be filled, and this decides whether it is filled
*correctly*.
"""

from typing import Any

import pytest

from meridian.authoring import interpret
from meridian.core.config import settings
from meridian.domain.graph import (
    ActionPrimitive,
    CheckPrimitive,
    EntityPrimitive,
    EventPrimitive,
    Primitive,
)

pytestmark = pytest.mark.llm


async def _mapped(card: Primitive, said: str) -> dict[str, Any]:
    """What this sentence does to this card, as stored config would look."""
    if not settings().openai_api_key:
        pytest.skip("no OPENAI_API_KEY — the offline tests cover the plumbing")

    patch = await interpret.fields(card, said)
    merged = type(card.config).model_validate(
        {**card.config.model_dump(mode="json"), **dict(patch)}
    )
    return merged.model_dump(mode="json", exclude_none=True)


async def test_an_arrival_is_waited_for_not_owed() -> None:
    """An event's clock counts down to something turning up.

    `await` and `sla` are the same field holding opposite meanings — one waits
    for a thing, the other owes somebody an answer — and the sentence mentions
    chasing, which is what makes it worth pinning. Chasing is what happens when
    a wait runs out, not a different kind of clock.
    """
    got = await _mapped(
        EventPrimitive(key="c1"),
        "They arrive by email — the subject line says Pre-Alert Documents, or sometimes "
        "APL USA // PRE-ALERT DOCUMENTATION. We give it 48 hours and if nothing shows up "
        "we chase it.",
    )

    assert got["channel"] == "email"
    assert got["timing"]["kind"] == "await"
    assert got["timing"]["deadline"] == "PT48H"
    assert got["name"]
    assert got["match_condition"]


async def test_one_per_the_case_is_simply_one() -> None:
    """ "One per <the case>" is the ordinary way to say there is exactly one.

    `one_per` means one for each item on *another* card, and it needs a field
    naming what they are counted against — a picker, because the model has never
    seen the board. Read this as `one_per` and the card is left needing an answer
    to a question its owner already gave.
    """
    got = await _mapped(
        EntityPrimitive(key="c2"),
        "It's the page headed Commercial Invoice. One of them per shipment.",
    )

    assert got["cardinality"]["kind"] == "one"
    assert got["name"]
    assert got["identified_by"]


async def test_waiting_for_paperwork_is_not_a_failure() -> None:
    """`on_missing_input` decides whether an absent input stops the process.

    The check is the card fill can help with least — everything that makes a
    check a check is a picker — so this is nearly the whole of what it may set,
    and the criteria in the sentence must stay untouched.
    """
    got = await _mapped(
        CheckPrimitive(key="c3"),
        "Review the Description of Goods section — every line item needs an HTS number, an "
        "FDA product code, an NDC number and an ANDA number. If the paperwork hasn't turned "
        "up yet we just wait for it.",
    )

    assert got["on_missing_input"] == "wait"
    assert got["name"]
    assert not got.get("criteria"), "criteria is a picker: a plausible wrong rule is undetectable"


async def test_writing_to_software_names_the_system_in_their_words() -> None:
    """`effect` is a closed set; `system` is free text, and the split matters.

    How a step moves data is ours to enumerate. What software a company runs is
    not, and an enum there would make somebody classify their own systems into
    our taxonomy.
    """
    got = await _mapped(
        ActionPrimitive(key="c4"),
        "Log an error into the warehouse system naming the invoice number, the drug "
        "description and which bit is missing. Mustn't log the same one twice.",
    )

    assert got["effect"] == "record"
    assert got["system"]
    assert not got.get("channel"), "nobody is being told anything"


async def test_a_person_on_a_clock_gets_a_deadline_and_somewhere_to_escalate() -> None:
    """The densest sentence here, and every field in it is load-bearing.

    Three things are being pinned. The two-day clock is on a **person**, so it
    is `timing.deadline` with `kind: sla` and never `timeout`, which is for a
    system call that might not answer. The escalation is its own field: a
    deadline with nothing to do when it passes compiles to a timer firing into
    nothing, which is the failure `on_timeout`'s finding exists to prevent — and
    it went missing until the prompt's own example named all three fields rather
    than the first two. And the recipient is a **role**, slugged, because an
    identity in a checksummed spec would make somebody leaving force a new spec
    version.
    """
    got = await _mapped(
        ActionPrimitive(key="c5"),
        "Report any missing or mismatched COA by email to the receiving supervisor, with the "
        "batch number and the invoice number. If they've not come back in two days it goes "
        "up to the ops manager.",
    )

    assert got["effect"] == "notify"
    assert got["channel"] == "email"
    assert got["timing"]["kind"] == "sla"
    assert got["timing"]["deadline"] == "P2D"
    assert not got.get("timeout"), "a person is being waited on, not a system call"
    assert got["on_timeout"], "a deadline with no answer is a timer firing into nothing"
    assert got["recipients"] == [{"kind": "role", "role": "receiving_supervisor"}]
    assert not got.get("payload_fields"), "field paths are pickers — they have to resolve"
