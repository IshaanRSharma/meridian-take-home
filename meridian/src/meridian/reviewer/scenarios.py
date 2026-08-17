"""Situations the board is asked to account for, derived from the graph.

A coverage floor, and deliberately not a model's work. Every outcome the process
owner declared is walked at least once, which is how an outcome nobody drew a
line out of gets found in round one rather than surviving because the reviewer's
attention was elsewhere. Enumerating this is cheap and exhaustive; asking for it
is neither. What needs domain knowledge — four certificates for three batches, a
correction arriving after the report has already gone — is the model's half, and
it is not here.

Keys are derived from the card and the outcome rather than from wording, because
a scenario is the unit a thread cites and re-runs to prove itself resolved. That
proof means nothing unless ``coas_valid_missing_coa`` describes the same walk in
round three as it did in round one.

Nothing here says where a scenario *ought* to end up. That is a statement about
the process rather than about the drawing, and only the process owner has it.
"""

from meridian.domain.graph import Board, CheckPrimitive
from meridian.domain.primitives import Outcome
from meridian.domain.review import Scenario

HAPPY_KEY = "happy_path"


def enumerate_from(board: Board) -> tuple[Scenario, ...]:
    """One clean run, and one situation per other way each check can come out.

    The first declared outcome is the clean one by convention — a process owner
    lists what they hope for first — so a variant is any other outcome, walked
    with every other check behaving.
    """
    checks = board.checks()
    clean: dict[str, str | tuple[str, ...]] = {
        check.key: check.config.outcomes[0].name for check in checks if check.config.outcomes
    }
    start = _start(board)

    found = [
        Scenario(
            key=HAPPY_KEY,
            kind="happy",
            description=_clean_sentence(checks),
            outcomes=dict(clean),
            start=start,
        )
    ]
    found.extend(
        Scenario(
            key=_variant_key(check, outcome),
            kind="variant",
            description=_variant_sentence(check, outcome),
            outcomes={**clean, check.key: _answers(board, check, outcome)},
            start=start,
        )
        for check in checks
        for outcome in check.config.outcomes[1:]
    )
    return tuple(found)


def _start(board: Board) -> str | None:
    """Where a walk begins, named only when the board offers a choice.

    ``dry_run`` refuses to pick between two entry points, and refusing is right:
    choosing silently drops the other one. Naming the first keeps every
    enumerated scenario runnable. Covering the *second* way in is a situation
    rather than a coverage floor, so it belongs to the model.
    """
    events = board.events()
    return events[0].key if len(events) > 1 else None


def _named(check: CheckPrimitive) -> str:
    """What the process owner called this card."""
    return check.config.name or check.key


def _clean_sentence(checks: tuple[CheckPrimitive, ...]) -> str:
    """The clean run, in the process owner's own words.

    Built from the card names they typed, never from keys: they read this to say
    whether the situation is one their process really has, and a key is a name
    only this repository knows.
    """
    answering = [check for check in checks if check.config.outcomes]
    if not answering:
        return "The process runs from start to finish with nothing to decide."
    said = " and ".join(
        f"{_named(check)} answers {check.config.outcomes[0].name!r}" for check in answering
    )
    return f"Nothing is wrong: {said}."


def _answers(board: Board, check: CheckPrimitive, outcome: Outcome) -> str | tuple[str, ...]:
    """How this check comes out — once, or once and then differently.

    A check the process can come back to needs two answers, not one. The whole
    point of the question that draws a repeat edge is "it comes back once the
    problem is fixed", and answering that check the same way every visit walks
    the loop until the step bound gives up and calls it `loop` — telling the
    owner their process never terminates, as a direct result of answering
    correctly. So on a cycle: wrong once, then right.
    """
    if check.key not in board.downstream(check.key):
        return outcome.name
    return (outcome.name, check.config.outcomes[0].name)


def _variant_key(check: CheckPrimitive, outcome: Outcome) -> str:
    """A stable name, short enough to be one.

    A scenario key is a `BoardKey`, and so are both halves of this — so two long
    ones overflow, and `enumerate_from` builds eagerly, meaning one unnameable
    scenario takes the entire round with it.
    """
    return f"{check.key[: 63 - len(outcome.name)]}_{outcome.name}"


def _variant_sentence(check: CheckPrimitive, outcome: Outcome) -> str:
    """One thing going wrong, described by whoever named the outcome."""
    said = f"{_named(check)} answers {outcome.name!r}"
    if outcome.description:
        said += f" — {outcome.description}"
    return f"{said}. Everything else is as it would be when nothing is wrong."


__all__ = ["HAPPY_KEY", "enumerate_from"]
