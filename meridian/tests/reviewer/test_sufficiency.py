"""Tests for whether a frozen spec is enough to build from.

The measurement Claude.md §33 specifies and nothing implemented: hand each card
to a model as its implementer and ask what it would have to decide for itself.

Two properties carry the file. A gap arrives with the card that forced it, because
"the spec is underspecified" is not actionable and "this check does not say what
counts as a match" is. And a contract that settles everything returns nothing —
without that half, a check that always finds something is indistinguishable from
one that is working, and nobody would ever be allowed to proceed.
"""

import json
from dataclasses import dataclass
from typing import Any

from meridian.compiler import serialize
from meridian.domain.graph import Board
from meridian.domain.review import Anchor, Assertion
from meridian.reviewer import sufficiency
from meridian.reviewer.sufficiency import Assumptions


@dataclass
class Turn:
    output: list[Any]
    output_parsed: Any = None


def model(*assumptions: dict[str, str]):
    """A fake implementer that reports these assumptions for every card."""
    parsed = Assumptions.model_validate({"assumptions": list(assumptions)})

    async def transport(**_: Any) -> Any:
        return Turn(output=[], output_parsed=parsed)

    return transport


def settles_everything():
    async def transport(**_: Any) -> Any:
        return Turn(output=[], output_parsed=Assumptions(assumptions=[]))

    return transport


async def test_a_gap_arrives_with_the_card_that_forced_it(complete: Board):
    spec = serialize.spec_payload(complete)

    gaps = await sufficiency.check(
        spec,
        transport=model(
            {"assumption": "I would compare the two strings exactly.", "breaks": "a trailing space"}
        ),
    )

    assert gaps
    assert {g.primitive_key for g in gaps} == set(spec.primitives)
    assert all(g.assumption and g.breaks for g in gaps)


async def test_a_contract_that_settles_everything_reports_nothing(complete: Board):
    # The half that makes the other half mean something. A check that always
    # finds something is indistinguishable from one that works.
    spec = serialize.spec_payload(complete)
    assert await sufficiency.check(spec, transport=settles_everything()) == ()


async def test_one_call_per_card_and_no_card_is_skipped(complete: Board):
    spec = serialize.spec_payload(complete)
    calls: list[str] = []

    async def counting(**kwargs: Any) -> Any:
        calls.append(kwargs["input"][0]["content"])
        return Turn(output=[], output_parsed=Assumptions(assumptions=[]))

    await sufficiency.check(spec, transport=counting)

    assert len(calls) == len(spec.primitives)


async def test_the_implementer_is_shown_the_card_what_it_reads_and_how_it_is_left(
    complete: Board,
):
    spec = serialize.spec_payload(complete)
    seen: dict[str, str] = {}

    async def capture(**kwargs: Any) -> Any:
        contract = json.loads(kwargs["input"][0]["content"])
        seen[contract["the_step_you_are_building"]["key"]] = kwargs["input"][0]["content"]
        return Turn(output=[], output_parsed=Assumptions(assumptions=[]))

    await sufficiency.check(spec, transport=capture)
    contract = json.loads(seen["coas_valid"])

    # The card itself, as codegen receives it.
    assert contract["the_step_you_are_building"]["primitive_type"] == "check"
    # What it reads, because a check cannot state a field schema itself.
    assert "commercial_invoice" in contract["things_it_reads"]
    # Where it can go, because a step cannot state which outcome leaves it.
    assert {"pass"} <= {o for e in contract["how_it_can_be_left"] for o in e["on_outcome"]}


async def test_a_card_is_not_shown_another_card_to_excuse_itself_with(complete: Board):
    # Deliberately one entry, not the whole spec: codegen reads it that way, and
    # a model handed everything can answer a gap in this card by pointing at
    # context that lives on a different one.
    spec = serialize.spec_payload(complete)
    seen: dict[str, str] = {}

    async def capture(**kwargs: Any) -> Any:
        contract = json.loads(kwargs["input"][0]["content"])
        seen[contract["the_step_you_are_building"]["key"]] = kwargs["input"][0]["content"]
        return Turn(output=[], output_parsed=Assumptions(assumptions=[]))

    await sufficiency.check(spec, transport=capture)

    assert "invoice_complete" not in seen["coas_valid"]


async def test_what_was_settled_about_a_card_travels_with_it(complete: Board):
    # The reason this catches compiler bugs and not only elicitation gaps: if the
    # transposition drops a statement, the entry is insufficient even though the
    # board was complete.
    settled = (
        Assertion(
            anchor=Anchor.parse("primitive:coas_valid"),
            kind="terminology",
            statement="A batch matches after trimming spaces and ignoring case.",
        ),
    )
    spec = serialize.spec_payload(complete, assertions=settled)
    seen: dict[str, str] = {}

    async def capture(**kwargs: Any) -> Any:
        contract = json.loads(kwargs["input"][0]["content"])
        seen[contract["the_step_you_are_building"]["key"]] = kwargs["input"][0]["content"]
        return Turn(output=[], output_parsed=Assumptions(assumptions=[]))

    await sufficiency.check(spec, transport=capture)

    assert "trimming spaces" in seen["coas_valid"]


def test_gaps_group_by_card_for_rendering():
    gaps = (
        sufficiency.Gap(primitive_key="a", assumption="one", breaks="x"),
        sufficiency.Gap(primitive_key="a", assumption="two", breaks="y"),
        sufficiency.Gap(primitive_key="b", assumption="three", breaks="z"),
    )
    grouped = sufficiency.by_card(gaps)

    assert list(grouped) == ["a", "b"]
    assert len(grouped["a"]) == 2


def test_the_prompt_asks_for_assumptions_not_wishes():
    # The distinction that decides whether the output is usable: "what would you
    # have to assume" is answerable and gates; "what would be nice to know" is a
    # wish list nobody can close.
    assert "ASSUME" in sufficiency.SYSTEM
    assert "must not be listed" in sufficiency.SYSTEM
