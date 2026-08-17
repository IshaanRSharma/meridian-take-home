"""The same claim as `test_mapping.py`, in a vertical nothing here has seen.

That file measures five sentences. They are pre-alert sentences, written by the
person who wrote the prompt, and a prompt is allowed to be good at the process
it was written beside. This file exists because that number could not tell the
difference between *the mapping works* and *the mapping was fitted*.

The process is a refrigeration contractor handling supermarket breakdown
call-outs. Field service rather than paperwork, and chosen by elimination: the
fill prompt's worked examples are insurance claims and licence checks,
`distill`'s are gym memberships and duty-manager approvals, and every board in
the repository is pharma logistics. Nothing in the package has been pointed at a
call-out, an engineer or a van.

Twelve sentences, weighted at what the first five never touched — `queue` and
`phone` and `sms` as channels, a scheduled event, `decide`, `noop`, a lookup's
`on_failure`, `performed_by`, `many` — and deliberately including the four
awkward ones: a clock on an arrival and a clock on a person, a lookup, a
cardinality relationship, and a step that writes to software.

**Measured at five runs a case. 40/42 fields stable at 5/5 first time; both
misses were the same case, both 0/5 rather than flaky. 42/42 now.** What broke
was `ActionDraft.effect`, whose description named four values of a five-value
enum: `decide` was simply absent, so *"needs the contracts manager to say yes"*
became `notify` — the nearest thing the model had been told existed. `on_timeout`
fell with it and recovered with it, because an escalation on a step that only
tells somebody something has nowhere to go.

Same root cause as both defects the first five sentences found: **an example or
a description that names only some of what a sentence settles teaches the model
that those are the whole answer.** Three for three, and so far the only class of
defect this has produced.

**The fix then broke the other vertical, which is the more useful half of this.**
Spelling out that a `notify` is a step nobody has to answer cost
`test_mapping.py`'s densest case its `on_timeout` — 5/5 to 0/5 — because
reporting something by email, chasing for two days and escalating is a genuine
`notify` **with** a clock. What actually separates the two effects is whether
the reply picks the next step, not whether anybody is waiting, and the
description now says that instead. Both verticals hold at 5/5 together: 42/42
here, 22/22 there. A wording that sharpens one process at the expense of another
is invisible without running both, so the five pre-alert sentences are measured
as a control on every change to this prompt, and belong in any future one.

**And five runs is not proof either.** A third defect surfaced on the twelfth
observation of a case that had measured 5/5 twice: *"if there's no registration
on file at all, that's a fail"* came back as `wait`. Its description said "what
to do **while** something it reads has not arrived" and named none of the three
answers, so the one it described was the one it got — the same root cause a
third time, at a rate low enough to hide behind n=5. It names all three now, and
the three check cases across both verticals measure 10/10.

Each test here runs once. Once is not the measurement — the numbers above are —
but a suite that spends five calls a case is a suite nobody runs. What n=1 is
good for is exactly what it did: run often enough that a rare wrong answer
eventually shows up as a red test.
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


# --- events ------------------------------------------------------------------


async def test_an_arrival_is_awaited_even_when_somebody_chases_it() -> None:
    """The same `await`-not-`sla` property as pre-alert, on a different channel.

    Worth repeating in a second vertical precisely because it is the one the
    prompt argues hardest for: chasing is what happens when a wait runs out, not
    a different kind of clock. `queue` also has to survive being described as
    somewhere a thing *lands* rather than as a queue.
    """
    got = await _mapped(
        EventPrimitive(key="c1"),
        "A call-out lands in the dispatch queue when a store logs a fault — it'll say which "
        "site it is and what's playing up. We reckon on having the fault details within the "
        "hour, and if they're still not there someone rings the store.",
    )

    assert got["channel"] == "queue"
    assert got["timing"]["kind"] == "await"
    assert got["timing"]["deadline"] == "PT1H"
    assert got["name"]
    assert got["match_condition"]


async def test_a_standing_sweep_is_scheduled_rather_than_arriving() -> None:
    """`mode` is the field nothing else in the suite reaches, and it is fragile.

    `_as_config` assembles `timing` from five flat keys and keeps the block only
    when `kind` is among them, because `Timing.kind` has no default and a block
    without one cannot validate. So a scheduled event that answers `mode` and
    `schedule` and leaves `kind` blank loses **all three** — the schedule
    included — and the card comes back looking as though nobody said when it
    runs. Asserting `kind` is set is what makes that silent drop visible; which
    of the two it is nobody has decided, so nothing here pretends to.
    """
    got = await _mapped(
        EventPrimitive(key="c2"),
        "Every weekday morning at six we sweep for call-outs still sitting open from the "
        "day before.",
    )

    assert got["timing"]["mode"] == "scheduled"
    assert got["timing"]["schedule"]
    assert got["timing"]["kind"]


# --- actions -----------------------------------------------------------------


async def test_a_lookup_carries_a_system_clock_and_a_way_out() -> None:
    """A clock on a SYSTEM, which is the other half of the pre-alert pair.

    `timeout` and `deadline` are the same sentence shape pointing at different
    things, and only one of them may be filled here. `on_failure` is the field
    that decides whether a process stops when a system does not answer, and
    *"just carry on"* is the answer a form would never have got.
    """
    got = await _mapped(
        ActionPrimitive(key="c3"),
        "Before we send anyone out we check the site's contract status in ServiceMax — give "
        "it half a minute, and if it doesn't come back just carry on and we'll sort the "
        "billing afterwards.",
    )

    assert got["effect"] == "lookup"
    assert got["system"]
    assert got["timeout"] == "PT30S"
    assert got["on_failure"] == "skip"
    assert not got.get("channel"), "nobody is being told anything"


async def test_asking_somebody_to_sign_off_is_a_decision_not_a_notification() -> None:
    """The case that failed, and the densest one here.

    Both fields it lost were lost together and for one reason: `effect`'s
    description listed four values of a five-value enum. A sign-off reached by
    text message is a notification to a model that has never been told `decide`
    exists — and then `on_timeout` has nothing to escalate, because a step that
    only tells somebody something is not waiting on them.

    `channel` is the trap and it is deliberate. How somebody is reached says
    nothing about whether the process is waiting on their reply, so a text
    message must not make this a notify.
    """
    got = await _mapped(
        ActionPrimitive(key="c4"),
        "Out-of-hours jobs over five hundred quid in parts need the contracts manager to say "
        "yes — we text them and give it three hours, and if nothing comes back it goes to "
        "the service director.",
    )

    assert got["effect"] == "decide"
    assert got["channel"] == "sms"
    assert got["performed_by"] == {"kind": "role", "role": "contracts_manager"}
    assert got["timing"]["kind"] == "sla"
    assert got["timing"]["deadline"] == "PT3H"
    assert not got.get("timeout"), "a person is being waited on, not a system call"
    assert got["on_timeout"], "a deadline with no answer is a timer firing into nothing"


async def test_writing_to_software_reaches_nobody() -> None:
    """`record` in a vertical where the system has a name nobody could guess.

    The negative half is the point: a `channel` on a step that reaches no person
    is a `minor` finding rather than a blank, so a wrong answer here is one a
    process owner would have to notice and un-set themselves.
    """
    got = await _mapped(
        ActionPrimitive(key="c5"),
        "Once the engineer's off site the visit gets written up in Sage Field Service "
        "against the job number, and the same visit mustn't go in twice.",
    )

    assert got["effect"] == "record"
    assert got["system"]
    assert not got.get("channel"), "nobody is being told anything"
    assert not got.get("timeout"), "nothing is being waited on"


async def test_a_phone_call_is_a_channel_like_any_other() -> None:
    """Two roles in one sentence, only one of which is a recipient.

    *"the engineer's name"* is a value that goes in the message and *"the store
    manager"* is who gets it. `payload_fields` is a picker, so the first must
    land nowhere; the second is slugged, because the bindings file keys on it
    and `store manager` and `store_manager` would be two entries for one person.
    """
    got = await _mapped(
        ActionPrimitive(key="c6"),
        "Before the engineer sets off we ring the store manager with the engineer's name and "
        "roughly when they'll be there.",
    )

    assert got["effect"] == "notify"
    assert got["channel"] == "phone"
    assert got["recipients"] == [{"kind": "role", "role": "store_manager"}]
    assert not got.get("payload_fields"), "field paths are pickers — they have to resolve"


async def test_a_named_ending_never_marks_itself_as_one() -> None:
    """`is_terminal` is the one field that closes a gate rather than opening one.

    A step nothing leads out of is a blocking finding, and `is_terminal` is what
    silences it. That makes it the single field fill may never set, however
    plainly the sentence says the process stops — so it is absent from every
    draft schema, and this is what proves the absence holds on the sentence most
    likely to break it.
    """
    got = await _mapped(
        ActionPrimitive(key="c7"),
        "If the unit's running again and the store's happy, that's the job finished — "
        "nothing else happens after that.",
    )

    assert got["effect"] == "noop"
    assert got["name"]
    assert not got.get("is_terminal"), "fill may open a gate and never close one"


# --- checks ------------------------------------------------------------------


async def test_a_check_can_wait_for_what_it_reads() -> None:
    """Nearly the whole of what a check may be filled with, and correctly so.

    The sentence states a comparison — fitted parts against van stock — and it
    has to land in `instructions` and nowhere else. A criterion that resolves
    and tests the wrong thing is caught by nothing downstream, which is why the
    field is a picker rather than a draft.
    """
    got = await _mapped(
        CheckPrimitive(key="c8"),
        "Check the parts the engineer fitted against what the van stock says was on board. "
        "If the stock file hasn't landed yet, leave it and pick it up later.",
    )

    assert got["on_missing_input"] == "wait"
    assert got["name"]
    assert not got.get("criteria"), "criteria is a picker: a plausible wrong rule is undetectable"
    assert not got.get("inputs"), "what a check reads is picked from the board, not guessed"


async def test_a_check_can_refuse_when_what_it_reads_is_absent() -> None:
    """The opposite answer, because one value proves only that a default is right.

    `wait` and `fail` are the same field carrying opposite process meanings —
    the shipment sits, or nobody is dispatched — and a sentence saying which is
    the only place that decision is ever made.
    """
    got = await _mapped(
        CheckPrimitive(key="c9"),
        "Make sure the engineer's Gas Safe registration covers the work before they go — if "
        "there's no registration on file at all, that's a fail and nobody's dispatched.",
    )

    assert got["on_missing_input"] == "fail"
    assert not got.get("criteria"), "criteria is a picker: a plausible wrong rule is undetectable"


# --- entities ----------------------------------------------------------------


async def test_one_for_each_item_on_another_card_is_one_per() -> None:
    """`one_per` with the counted-against field left for a picker.

    The relationship belongs to neither card alone, so the kind is drafted and
    `per` — a `FieldRef` into another entity the model has never been shown — is
    not. `Cardinality.findings()` then reports exactly that gap, which is the
    split working rather than a half-filled answer.
    """
    got = await _mapped(
        EntityPrimitive(key="c10"),
        "Every unit we're asked to look at gets its own signed job sheet — one per unit "
        "listed on the docket.",
    )

    assert got["cardinality"]["kind"] == "one_per"
    assert got["name"]
    assert not got["cardinality"].get("per"), "which field enumerates them is a picker"


async def test_no_number_at_all_is_many() -> None:
    """The third value, and the one a card carries no default for.

    `many` is what a process owner says by refusing to commit to a count, and
    *"could be two, could be twenty"* is that refusal. Read as `one` it silently
    caps a real process at one.
    """
    got = await _mapped(
        EntityPrimitive(key="c11"),
        "Engineers send photos back from site — could be two, could be twenty, depends what "
        "they find.",
    )

    assert got["cardinality"]["kind"] == "many"


async def test_one_per_the_case_survives_a_case_the_prompt_never_named() -> None:
    """The honest version of `test_mapping.py::test_one_per_the_case_is_simply_one`.

    That test asks whether *"one of them per shipment"* is `one`, and until this
    was written the field's own description answered it in those words — the
    demo customer's noun, in the prompt, in the field being measured. It said
    what it was measuring and measured this repository instead.

    Nothing tells the model that a call-out is a run of the process. It has to
    read that off the sentence, which is the property the other test was
    supposed to have.
    """
    got = await _mapped(
        EntityPrimitive(key="c12"),
        "There's one dispatch note per call-out. It's the sheet with the job number at the "
        "top and the site address underneath.",
    )

    assert got["cardinality"]["kind"] == "one"
    assert got["identified_by"]
