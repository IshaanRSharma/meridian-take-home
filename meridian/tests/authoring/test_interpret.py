"""Reading a sentence onto a card's fields.

Everything here runs offline against a fake transport, so the loop is exercised
for free and no test can quietly spend money.

Two properties carry the file, and the first is the design rather than a
behaviour: **the model is never offered a field it may not set.** Not filtered
afterwards — absent from the schema, so a hallucinated field path or an invented
outcome is unrepresentable. That is asserted against the `ALLOWED` table so the
table and the four schemas cannot drift apart.

The second is what happens when it gets something wrong: **the bad value drops
and the rest survives.** One malformed duration must not cost the owner the other
three fields, and a dropped field is simply blank — which is a lint finding,
which is a question somebody was going to be asked anyway. There is no error
dialog on this path.
"""

import json
from dataclasses import dataclass
from typing import Any

import pytest

from meridian.authoring import interpret
from meridian.domain.graph import (
    ActionPrimitive,
    CheckPrimitive,
    EntityPrimitive,
    EventPrimitive,
    Primitive,
)
from meridian.domain.primitives import ActionConfig, CheckConfig, EntityConfig, EventConfig


@dataclass
class Turn:
    output: list[Any]
    output_parsed: Any = None


class FakeTransport:
    """Serves one canned draft and records what it was sent."""

    def __init__(self, draft: Any) -> None:
        self.draft = draft
        self.sent: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        return Turn(output=[], output_parsed=self.draft)


def action(**config: Any) -> Primitive:
    return ActionPrimitive(key="tell_them", config=ActionConfig(**config))


def event(**config: Any) -> Primitive:
    return EventPrimitive(key="it_arrives", config=EventConfig(**config))


def check(**config: Any) -> Primitive:
    return CheckPrimitive(key="is_it_ok", config=CheckConfig(**config))


def entity(**config: Any) -> Primitive:
    return EntityPrimitive(key="the_form", config=EntityConfig(**config))


# --- what the model is allowed to touch --------------------------------------


@pytest.mark.parametrize("kind", ["event", "action", "check", "entity"])
def test_the_model_is_never_offered_a_field_it_may_not_set(kind: str) -> None:
    # The allowlist made executable. A field absent from the schema cannot be
    # returned at all, which is a stronger guarantee than filtering one out —
    # and this is what stops the two drifting apart.
    offered = set(interpret.DRAFTS[kind].model_fields)
    reachable = interpret.ALLOWED[kind]

    # The flat draft reassembles into config shape, so compare what it can reach.
    flattened = {"timing_kind", "timing_mode", "deadline", "max_lifetime", "schedule"}
    renamed = {"recipient_roles": "recipients", "cardinality_kind": "cardinality"}
    reaches = {renamed.get(f, f) for f in offered - flattened}
    if offered & flattened:
        reaches.add("timing")

    assert reaches == reachable


def test_a_check_offers_almost_nothing() -> None:
    # Correct rather than an oversight. Everything that makes a check a check is
    # somewhere a plausible wrong answer is undetectable, so a person picks it.
    assert interpret.ALLOWED["check"] == {"name", "on_missing_input"}


@pytest.mark.parametrize("kind", ["event", "action", "check", "entity"])
def test_nothing_that_must_resolve_is_on_offer(kind: str) -> None:
    # A bad field path is caught by lint and a bad role by `bind check`, but a
    # bad criterion is caught by nothing: it resolves, it is well formed, and it
    # tests the wrong thing.
    forbidden = {
        "criteria",
        "outcomes",
        "inputs",
        "captures",
        "scope",
        "quantifier",
        "evidence",
        "fills",
        "payload_fields",
        "correlation_key",
        "produces",
    }
    assert not forbidden & set(interpret.DRAFTS[kind].model_fields)
    assert not forbidden & interpret.ALLOWED[kind]


@pytest.mark.parametrize("kind", ["event", "action", "check", "entity"])
def test_the_model_can_never_mark_something_as_an_ending(kind: str) -> None:
    # `is_terminal` is the only config field that *suppresses* a blocking
    # finding. Fill may open a gate; it may never close one.
    assert "is_terminal" not in interpret.DRAFTS[kind].model_fields


@pytest.mark.parametrize("kind", ["event", "action", "check", "entity"])
def test_the_model_is_never_asked_to_write_the_instructions(kind: str) -> None:
    # A summarised sentence is a destroyed binding brief. This module writes it
    # from what was said, so the model has no opportunity to paraphrase it.
    assert "instructions" not in interpret.DRAFTS[kind].model_fields


# --- what it does with a sentence --------------------------------------------


async def test_a_sentence_becomes_fields() -> None:
    said = "We email the receiving supervisor, and it escalates after two days."
    transport = FakeTransport(
        interpret.ActionDraft(
            name="Tell the supervisor",
            effect="notify",
            channel="email",
            recipient_roles=["receiving_supervisor"],
            timing_kind="sla",
            deadline="P2D",
        )
    )

    patch = await interpret.fields(action(), said, transport=transport)

    assert patch["effect"] == "notify"
    assert patch["channel"] == "email"
    assert patch["recipients"] == [{"kind": "role", "role": "receiving_supervisor"}]
    assert patch["timing"] == {"kind": "sla", "deadline": "P2D"}


async def test_a_person_is_never_stored_as_a_recipient() -> None:
    # Roles, never identities: the spec is checksummed, so a leaver would
    # otherwise force a new spec version. An `EventRef` is unreachable too.
    transport = FakeTransport(interpret.ActionDraft(recipient_roles=["ops_manager"]))

    patch = await interpret.fields(action(), "the ops manager hears about it", transport=transport)

    assert all(r["kind"] == "role" for r in patch["recipients"])


async def test_the_owners_words_are_kept_exactly() -> None:
    said = "Nursys for most states, but California and Texas have their own portals."
    transport = FakeTransport(interpret.ActionDraft(effect="lookup"))

    patch = await interpret.fields(action(), said, transport=transport)

    # The residual is the binding brief — one capability, three backends, state
    # routing. A fill that discarded it after extracting `effect` destroys it.
    assert patch["instructions"] == said


async def test_describing_a_card_twice_keeps_both_sentences() -> None:
    transport = FakeTransport(interpret.ActionDraft())
    card = action(instructions="We email the supervisor.")

    patch = await interpret.fields(card, "It escalates after two days.", transport=transport)

    assert patch["instructions"] == "We email the supervisor.\n\nIt escalates after two days."


async def test_nothing_said_makes_no_call_at_all() -> None:
    async def never(**_: Any) -> Any:
        raise AssertionError("no words, no call")

    assert await interpret.fields(action(), "   ", transport=never) == {}


# --- when it gets something wrong --------------------------------------------


async def test_a_value_the_model_got_wrong_drops_and_the_rest_survives() -> None:
    # "two days" instead of P2D. One bad field must not cost the owner the other
    # three — and a dropped field is blank, which is already a lint finding.
    transport = FakeTransport(
        interpret.ActionDraft(
            effect="notify", channel="email", timing_kind="sla", deadline="two days"
        )
    )

    patch = await interpret.fields(action(), "email them, two days", transport=transport)

    assert patch["effect"] == "notify"
    assert patch["channel"] == "email"
    assert "timing" not in patch


async def test_a_patch_that_is_wrong_all_through_still_keeps_the_words() -> None:
    transport = FakeTransport(interpret.ActionDraft(timing_kind="sla", deadline="whenever"))

    patch = await interpret.fields(action(), "sometime soon", transport=transport)

    assert patch == {"instructions": "sometime soon"}


# --- what it refuses to overwrite --------------------------------------------


async def test_a_field_that_is_already_set_is_left_alone() -> None:
    # Overwriting what somebody typed, or picked from a dropdown, is the exact
    # moment an accelerator becomes an authority.
    transport = FakeTransport(interpret.ActionDraft(channel="sms"))
    card = action(effect="notify", channel="email")

    patch = await interpret.fields(card, "text them instead", transport=transport)

    assert "channel" not in patch


async def test_overwrite_replaces_it_when_that_is_what_they_meant() -> None:
    transport = FakeTransport(interpret.ActionDraft(channel="sms"))
    card = action(effect="notify", channel="email")

    patch = await interpret.fields(card, "no, text them", overwrite=True, transport=transport)

    assert patch["channel"] == "sms"


# --- what the model is shown --------------------------------------------------


async def test_the_model_is_shown_no_key_it_could_return() -> None:
    # `distill` hands the model a list of valid anchors precisely so it stops
    # guessing keys. Here the opposite is wanted: a model that has never seen a
    # key cannot return one, which makes the guarantee structural.
    transport = FakeTransport(interpret.CheckDraft())

    await interpret.fields(check(), "every line needs a code", transport=transport)

    sent = json.dumps(transport.sent[0]["input"])
    assert "commercial_invoice" not in sent
    assert "coas_valid" not in sent


async def test_the_model_is_shown_what_is_already_answered() -> None:
    # So it can leave those alone rather than proposing them again and having
    # them dropped, which would waste the call and read as if it had ignored.
    transport = FakeTransport(interpret.EventDraft())

    await interpret.fields(event(channel="email"), "it arrives by email", transport=transport)

    assert "email" in json.dumps(transport.sent[0]["input"])


# --- the other card types -----------------------------------------------------


async def test_an_entity_gets_how_many_to_expect_but_not_what_it_is_counted_against() -> None:
    # `per` is a FieldRef and stays a picker. `Cardinality.findings()` already
    # reports the gap as "nothing says what these are counted against" — the
    # split was anticipated before it existed.
    transport = FakeTransport(
        interpret.EntityDraft(
            name="Certificate",
            identified_by="the header reads Certificate",
            cardinality_kind="one_per",
        )
    )

    patch = await interpret.fields(entity(), "one per batch on the invoice", transport=transport)

    assert patch["cardinality"] == {"kind": "one_per"}
    assert "per" not in patch["cardinality"]


async def test_a_check_gets_what_to_do_while_waiting() -> None:
    transport = FakeTransport(interpret.CheckDraft(name="Is it ok", on_missing_input="wait"))

    patch = await interpret.fields(check(), "if it hasn't arrived we wait", transport=transport)

    assert patch == {
        "name": "Is it ok",
        "on_missing_input": "wait",
        "instructions": "if it hasn't arrived we wait",
    }
