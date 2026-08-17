"""Whether the frozen spec is enough to build from.

Every other number about this system measures the reviewer's own behaviour —
recall on blanks, noise on a finished board, how much the review added on top of
the drawing. None of them measures the thing that is actually delivered, which is
a spec somebody else has to implement.

Claude.md §33 names the oracle: hand `spec.lock.json` to a fresh agent with no
other context, and if it asks a business question the spec is incomplete. This is
that, run per card and automatically.

**The model is an implementer here, not a reviewer.** It is not asked what the
process is missing — that question belongs before the freeze and has a person to
answer it. It is asked what it would have to *decide for itself* to write this
card's file. Everything it had to assume is something two competent implementers
would settle differently, which is the definition of an underspecified contract.

Per card rather than per spec, for the same reason codegen reads it per card: one
`SpecPrimitive` is the whole context for one file, joined to nothing. A gap found
that way arrives with a location.

It also catches a class of bug no board-level check can see. The freeze
transposes conversation-shaped knowledge into card-shaped knowledge, and a
statement that reaches no card is dropped silently — that has happened twice in
this repository's history, to edge-anchored and entity-anchored statements. A
board can be complete while the spec built from it is not, and the only way to
notice is to read the spec as its consumer does.
"""

import json

from pydantic import BaseModel, Field

from meridian.core.llm import Task, Transport, structured
from meridian.domain.frozen import FrozenSpec, SpecPrimitive

SYSTEM = """\
You are about to write the code for one step of a business process, from the
contract below. Somebody else agreed that contract with the business; you cannot
ask them anything, and what you write is what ships.

Read it as an implementer. List every decision you would have to make yourself
that the contract does not settle — the things where you would write something,
it would run, and nobody would notice it was a guess until it was wrong in
production.

What counts:

  · a comparison whose exact rule is not stated — what makes two values the
    same, what counts as present, what tolerance applies
  · an ordering, a limit or a duration nobody named
  · behaviour in a case the contract does not mention, that your code must
    nonetheless do something about
  · a value you would have to invent a format or a default for
  · two statements in the contract that you cannot satisfy at once

What does not count, and must not be listed:

  · anything the contract DOES settle, anywhere in it — including in prose. The
    statements under `context` are part of the contract, not commentary.
  · how to structure your code, which library to reach for, how to handle a
    timeout — those are yours to decide and always were.
  · a wish for more context. The question is what you would have to ASSUME, not
    what would have been nice to read.

For each one, say what you would assume and what breaks if the assumption is
wrong. Be specific: "I would compare the two strings exactly, so a trailing
space would report a mismatch that is not one" — never "matching is ambiguous".

If the contract settles everything, return nothing. That is a real answer and the
one worth reaching.\
"""


class Assumption(BaseModel):
    """One decision the contract left to whoever implements it."""

    assumption: str = Field(description="what you would decide, stated as a decision")
    breaks: str = Field(description="what goes wrong if that decision is the wrong one")


class Assumptions(BaseModel):
    """Everything one card left to its implementer."""

    assumptions: list[Assumption] = Field(default_factory=list)


class Gap(BaseModel):
    """One assumption, and the card that forced it."""

    primitive_key: str
    assumption: str
    breaks: str


async def check(spec: FrozenSpec, *, transport: Transport | None = None) -> tuple[Gap, ...]:
    """Every decision this spec leaves to whoever implements it.

    One call per card, in the spec's own order, so a gap arrives with a location
    and one unanswerable card does not swallow the rest.

    Args:
        spec: the frozen spec, exactly as a code generator receives it.
        transport: the function that talks to OpenAI. Tests pass a fake.

    Returns:
        One gap per assumption, keyed to the card that forced it. Empty means the
        spec settles everything, which is the number worth reaching.
    """
    found: list[Gap] = []
    for key, card in spec.primitives.items():
        answered = await structured(
            Task.REVIEW, Assumptions, SYSTEM, _contract(spec, key, card), transport=transport
        )
        found += [
            Gap(primitive_key=key, assumption=one.assumption, breaks=one.breaks)
            for one in answered.value.assumptions
        ]
    return tuple(found)


def _contract(spec: FrozenSpec, key: str, card: SpecPrimitive) -> str:
    """One card exactly as codegen receives it, plus what it reads and where it sits.

    Deliberately not the whole spec. Codegen reads one entry and joins nothing,
    so handing over more than that would test a contract nobody uses — and would
    let the model excuse a gap in this card by pointing at another one.

    The entities it reads and the edges it sits on are the exception, because
    they are not context the card *could* have carried: a check cannot state a
    field schema, and a step cannot state which outcome leaves it.
    """
    reads = tuple(getattr(card.config, "inputs", ()) or ()) + tuple(
        getattr(card.config, "captures", ()) or ()
    )
    return json.dumps(
        {
            "the_step_you_are_building": card.model_dump(mode="json", exclude_none=True),
            "things_it_reads": {
                entity: spec.entities[entity].model_dump(mode="json", exclude_none=True)
                for entity in reads
                if entity in spec.entities
            },
            "how_it_can_be_left": [
                {
                    "on_outcome": list(edge.on_outcomes),
                    "relation": edge.relation,
                    "goes_to": edge.to_key,
                    "settled_about_this_transition": list(
                        spec.edge_context[edge.key].local if edge.key in spec.edge_context else ()
                    ),
                }
                for edge in spec.edges
                if edge.from_key == key
            ],
        },
        indent=2,
    )


def by_card(gaps: tuple[Gap, ...]) -> dict[str, list[Gap]]:
    """The same gaps grouped by the card that forced them, for rendering."""
    grouped: dict[str, list[Gap]] = {}
    for gap in gaps:
        grouped.setdefault(gap.primitive_key, []).append(gap)
    return grouped


__all__ = ["Assumption", "Assumptions", "Gap", "by_card", "check"]
