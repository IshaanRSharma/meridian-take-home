"""What the reviewer may do besides read.

A tool here is not a convenience. The whole board is already in the payload, so
anything the model could assemble for itself would only be a shortcut — and a
shortcut it may take or skip, which makes the questions that depend on it
unreliable. Each of these earns its place by handing over something the model
**cannot** get from reading:

``dry_run`` walks a situation. Without it the model can only assert that a
situation ends somewhere, and an assertion is a claim the owner argues with. A
walk it had to ask for is one they can check.

``where_values_meet`` collects every point on the board where two values are
compared or joined. That sounds derivable and is not: the join points live in
five unrelated config shapes — a matching criterion, a field-to-field
comparison, a cardinality counted against another entity's field, an event's
correlation key, and prose reading across two entities — and nothing anywhere
puts them in one list. The correlation key in particular is the largest join on
any board and appears nowhere as a comparison, so a model reading configs one at
a time will not see it as one.

Both are returned as ordinary data. Neither writes anything, and neither decides
anything: a tool result is evidence, and what to make of it stays the model's
job.
"""

from collections.abc import Sequence
from typing import Any

from meridian.core.llm import Tool
from meridian.domain.graph import Board, DryRunResult, EntityPrimitive, EventPrimitive
from meridian.domain.review import Anchor, Assertion, Scenario
from meridian.reviewer import dryrun


def for_board(
    board: Board,
    probed: dict[str, tuple[Scenario, DryRunResult]],
    settled: Sequence[Assertion] = (),
) -> tuple[Tool, ...]:
    """Everything the model may call while reviewing this board."""
    return (_walking(board, probed), _matching(board, settled))


# ── walking a situation ──────────────────────────────────────────────────────


def _walking(board: Board, probed: dict[str, tuple[Scenario, DryRunResult]]) -> Tool:
    """Walk a situation the model invented, and keep it so it can be re-run.

    The probe is stored under a key the tool assigns rather than one the model
    names, because the key is what a question cites and a citation the model
    could mint is a citation it could fabricate.
    """

    async def walk(arguments: dict[str, Any]) -> dict[str, Any]:
        key = f"probe_{len(probed) + 1}"
        # A list of pairs rather than a map, because a strict tool schema forbids
        # open-ended objects. It reads better anyway: answering a check once and
        # answering it differently on each visit stop being the same field.
        answers: dict[str, str | tuple[str, ...]] = {}
        for given in arguments.get("outcomes") or ():
            said = tuple(given.get("answers") or ())
            answers[given["check"]] = said[0] if len(said) == 1 else said

        scenario = Scenario(
            key=key,
            kind="probe",
            description=arguments.get("describe") or "a situation the reviewer tried",
            outcomes=answers,
            start=arguments.get("start"),
        )
        result = dryrun.walk(board, scenario)
        probed[key] = (scenario, result)
        steps = [step.key for step in result.trace]
        return {
            # Cite this key on any question that came out of this walk. It is the
            # only way the question can be re-run later to show it was answered.
            "key": key,
            "result": result.result,
            "path": dryrun.path(board, result.trace),
            "along_the_way": dryrun.data_flow(board, steps),
            "never_reached": list(result.unreached),
            "why_it_stopped": next(
                (step.note for step in reversed(result.trace) if step.note), None
            ),
        }

    return Tool(
        name="dry_run",
        description=(
            "Walk a situation over this board and find out what happens. Use "
            "this to investigate, not just to confirm — describe a situation "
            "you think the process might not handle and see. `outcomes` says "
            "how each check comes out; give several answers for one check to "
            "answer it differently each time it is reached, which is how a "
            "correction arriving later is described. Returns the path taken "
            "with its arrows named, what data every step on it reads and "
            "writes, everything the walk never reached, and why it stopped."
        ),
        parameters={
            "type": "object",
            "properties": {
                "outcomes": {
                    "type": "array",
                    "description": "how each check comes out in this situation",
                    "items": {
                        "type": "object",
                        "properties": {
                            "check": {"type": "string", "description": "the check's key"},
                            "answers": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "one outcome name, or several to answer the same "
                                    "check differently each time it is reached"
                                ),
                            },
                        },
                        "required": ["check", "answers"],
                        "additionalProperties": False,
                    },
                },
                "start": {
                    "type": ["string", "null"],
                    "description": "which event to begin at, when the board has more than one",
                },
                "describe": {
                    "type": ["string", "null"],
                    "description": (
                        "one plain sentence naming the situation, as a process owner "
                        "would say it — this is stored and re-run later"
                    ),
                },
            },
            "required": ["outcomes", "start", "describe"],
            "additionalProperties": False,
        },
        run=walk,
    )


# ── where two values meet ────────────────────────────────────────────────────


def _matching(board: Board, settled: Sequence[Assertion]) -> Tool:
    """Every point two values are compared or joined, and what is said about it."""

    async def meeting_points(_: dict[str, Any]) -> list[dict[str, Any]]:
        return where_values_meet(board, settled)

    return Tool(
        name="where_values_meet",
        description=(
            "List every point in this process where two values have to be "
            "matched, joined or compared with each other — and what, if "
            "anything, has already been said about how. Takes no arguments and "
            "returns all of them. These are scattered across different parts of "
            "the drawing and are easy to miss one at a time: a check that pairs "
            "up two sets of things, a comparison of one field against another, "
            "a count of one thing per something on another, and the value every "
            "arriving thing is filed under. A meeting point with nothing said "
            "about it is a rule somebody knows and nobody wrote down."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        run=meeting_points,
    )


def where_values_meet(board: Board, settled: Sequence[Assertion] = ()) -> list[dict[str, Any]]:
    """Every join on the board, in board order, with what has been settled about it.

    Five shapes, because a value gets matched to another value in five different
    places in the schema and no consumer sees more than one of them at a time.
    Listed together they are obviously the same question asked five ways: *how
    do you know these two are the same thing.*
    """
    found: list[dict[str, Any]] = []
    for card in board.primitives:
        if isinstance(card, EventPrimitive):
            found += _from_event(card)
        elif isinstance(card, EntityPrimitive):
            found += _from_entity(card)
        else:
            found += _from_criteria(card)
    return [point | {"settled": _settled_about(point, settled)} for point in found]


def _from_event(card: EventPrimitive) -> list[dict[str, Any]]:
    """The correlation key: one value that has to be recognised on everything.

    The largest join on any board and the one nothing calls a comparison. Every
    thing that arrives has to be matched to the case already in progress, and the
    board never says how that value is found on each of them.
    """
    key = card.config.correlation_key
    if key is None:
        return []
    return [{"where": f"primitive:{card.key}", "how": "correlation_key", "sides": [str(key)]}]


def _from_entity(card: EntityPrimitive) -> list[dict[str, Any]]:
    """How many of this thing to expect, counted against a field on another one."""
    cardinality = card.config.cardinality
    if cardinality.kind != "one_per" or cardinality.per is None:
        return []
    return [
        {
            "where": f"primitive:{card.key}",
            "how": "one_per",
            "sides": [card.key, str(cardinality.per)],
        }
    ]


def _from_criteria(card: Any) -> list[dict[str, Any]]:
    """Matching and comparing inside a check.

    A comparison against a literal is not a meeting point — one side is a
    constant, so there is nothing to recognise as the same thing. Prose counts
    only when it reads across more than one entity, which is the same test in
    the one place the schema cannot apply it.
    """
    criteria = getattr(card.config, "criteria", ()) or ()
    found: list[dict[str, Any]] = []
    for criterion in criteria:
        against = criterion.right.field if criterion.right is not None else None
        if criterion.op == "custom":
            if len({ref.entity for ref in criterion.reads}) > 1:
                found.append(
                    {
                        "where": f"primitive:{card.key}",
                        "how": "described_in_words",
                        "sides": [str(ref) for ref in criterion.reads],
                        "the_owner_wrote": criterion.statement,
                    }
                )
        elif criterion.left is not None and against is not None:
            found.append(
                {
                    "where": f"primitive:{card.key}",
                    "how": criterion.op,
                    "sides": [str(criterion.left), str(against)],
                    "compared_by": criterion.operator,
                }
            )
    return found


def _settled_about(point: dict[str, Any], settled: Sequence[Assertion]) -> list[str]:
    """Statements bearing on either side of this join, or on the card holding it.

    An empty list is the finding. Everything else in the review reports what is
    *missing from a field*; this reports a rule missing from a **relationship**,
    which has no field to be missing from and so is invisible to every lint rule
    there is.
    """
    sides = set(point["sides"])
    where = Anchor.parse(point["where"])
    return [
        f"[{a.kind}] {a.statement}"
        for a in settled
        if a.is_active() and _bears_on(a.anchor, sides, where)
    ]


def _bears_on(anchor: Anchor, sides: set[str], where: Anchor) -> bool:
    if anchor.kind == "board":
        return True
    if anchor.kind == "entity_field":
        return (anchor.key or "") in sides
    return anchor.kind == where.kind and anchor.key == where.key


__all__ = ["for_board", "where_values_meet"]
