"""Walking a situation over the board, and citing the walk afterwards.

The walk belongs to ``Board.dry_run``. What is here is the two things a reviewer
needs on top of it.

A scenario it cannot run must not stop the round. ``dry_run`` raises when a
board has several ways in and the scenario names none, which is right for a
caller that can fix it and wrong for a loop walking a list — one unrunnable
scenario would take every other question with it.

And a question about structure may only claim what someone else can reproduce.
The citation carries the call, not a description of the call: same board, same
arguments, same answer, or the thread is dropped before it reaches the process
owner.
"""

from collections.abc import Sequence
from itertools import pairwise

from meridian.domain.errors import IncompleteError, NotFoundError
from meridian.domain.graph import Board, DryRunResult, TraceStep
from meridian.domain.primitives import FieldRef
from meridian.domain.review import Evidence, Scenario

Walked = Sequence[tuple[Scenario, DryRunResult]]

_COLUMN = " " * 17
"""Where a described situation's values start, so a wrapped path lines up."""


def walk(board: Board, scenario: Scenario) -> DryRunResult:
    """Where this situation ends up on this board.

    A scenario with nowhere to start reports as a dead end, which is what it is:
    the process was never entered and nothing was reached. That is the same
    answer ``dry_run`` gives a board with no way in at all, and the reviewer
    treats both the same way — it has a question either way.

    A scenario naming a card that is gone reports the same, and that is not an
    edge case: cards are deliberately deleted and re-created during review, and
    edges are deliberately left behind pointing at nothing so the owner decides
    which was the mistake. So the sanctioned mid-review board is exactly the one
    that would otherwise raise here — and take every other question in the round
    with it, one round before the finding that explains why.
    """
    try:
        return board.dry_run(scenario.outcomes, scenario.start)
    except (IncompleteError, NotFoundError):
        return DryRunResult(result="dead_end", unreached=tuple(node.key for node in board.nodes()))


def evidence_for(scenario: Scenario, result: DryRunResult) -> Evidence:
    """The citation for a walk, carrying enough of it to be run again.

    ``outcomes`` and ``start`` are the arguments verbatim, so re-running is a
    matter of passing them back. Tuples become lists because this is written to
    jsonb and read back before anyone doubts it, and ``dry_run`` takes any
    sequence for exactly that reason.
    """
    return Evidence(
        tool="dry_run",
        result=result.result,
        detail={
            "scenario": scenario.key,
            "start": scenario.start,
            "outcomes": {
                key: list(answer) if isinstance(answer, tuple) else answer
                for key, answer in scenario.outcomes.items()
            },
            "trace": [step.key for step in result.trace],
        },
    )


def reproduces(board: Board, evidence: Evidence) -> bool:
    """Whether this citation still says what it said when it was written.

    The one operation that makes `resolved` mean something. A question was
    raised because a situation ended somewhere; run it again against the board
    as it now stands, and if it ends somewhere else the drawing has changed in
    the way the answer required. If it ends the same way, nothing was fixed and
    the owner saying so does not make it so.

    A citation that cannot be run at all counts as still reproducing: it is not
    evidence that anything changed, and quietly resolving on a broken re-run
    would be the worst possible way to lose a question.
    """
    if evidence.tool != "dry_run":
        return True
    detail = evidence.detail or {}
    outcomes = detail.get("outcomes")
    if not isinstance(outcomes, dict):
        return True

    start = detail.get("start")
    walked = walk(
        board,
        Scenario(
            key="recheck",
            kind="probe",
            description="the situation that raised a question, walked again",
            outcomes={
                key: tuple(answer) if isinstance(answer, list) else str(answer)
                for key, answer in outcomes.items()
            },
            start=start if isinstance(start, str) else None,
        ),
    )
    return walked.result == evidence.result


def describe(board: Board, walked: Walked) -> str:
    """Each situation as a path, and what every step on it does with the data.

    A semantic question is nearly always about a relationship between two points
    on a path — *how do you match this to that, by the time you get here.* Handed
    an adjacency list the model has to rebuild the shape before it can ask, and
    it sometimes rebuilds it wrong. Handed the path, it reasons about the thing
    itself.

    What a step *tests* is kept apart from what it *reports as proof*: rolled
    together, the model asks why an identifier it only quotes back has no rule,
    which is a question about a field that was never meant to have one.

    ``never reached`` earns its line — "a failing first check means the second
    one never runs" is invisible in a trace and obvious here.
    """
    return "\n\n".join(_described(board, scenario, result) for scenario, result in walked)


def path(board: Board, trace: Sequence[TraceStep]) -> str:
    """The walk: each step, how it came out, and the arrow it left by.

    Both halves are load-bearing for a model reading this.

    The arrow is the edge the walk **recorded**, never one worked back out of the
    pair of steps it joins. Re-deriving it returns whichever edge was drawn
    first, so a board routing two outcomes to one step described every walk
    through that pair as the same arrow — including the walk that took the other
    one.

    The answer sits beside the step that gave it because otherwise two
    situations differing only in how one check came out render as the same
    sentence, and that difference is the whole question. On the pre-alert board
    a missing certificate and a mismatched one lead to the same reporting step,
    and whether those deserve different handling is exactly what is being asked.

    One line per transition, so a walk that goes round a loop stays readable —
    the same step appearing twice with two different answers is the point of
    those walks, and on one line it is lost. The step it started at has no arrow
    into it, so it shares the first line.
    """
    if not trace:
        return "nothing"
    hops = [f"{_arrow(board, before.via)} {_step(after)}" for before, after in pairwise(trace)]
    if not hops:
        return _step(trace[0])
    return "\n".join([f"{_step(trace[0])} {hops[0]}", *hops[1:]])


def data_flow(board: Board, steps: Sequence[str]) -> list[str]:
    """What each step on this path does with the data, in its own vocabulary."""
    said: list[str] = []
    walkable = {node.key: node for node in board.nodes()}
    for key in steps:
        # A trace only ever names steps, but `Board.p` cannot know that — and an
        # entity has no references to describe.
        card = walkable.get(key)
        if card is None:
            continue
        references = board.field_references(card)
        if card.primitive_type == "event":
            said.append(f"{key:<26} brings in  {', '.join(sorted(card.produces())) or '—'}")
            continue
        tested = _refs(references, ("criteria", "payload_fields", "correlation_key"))
        proof = _refs(references, ("evidence",))
        verb = "sends" if card.primitive_type == "action" else "tests"
        said.append(f"{key:<26} {verb:<10} {tested or '—'}")
        if proof:
            said.append(f"{'':<26} {'reports':<10} {proof}")
    return said


def _described(board: Board, scenario: Scenario, result: DryRunResult) -> str:
    steps = [step.key for step in result.trace]
    walked = path(board, result.trace).replace("\n", f"\n{_COLUMN}")
    lines = [
        f"{scenario.key}  ({scenario.kind})",
        f"  what happens   {scenario.description}",
        f"  path           {walked}",
        f"  ended          {result.result}",
        f"  never reached  {', '.join(result.unreached) or 'nothing'}",
    ]
    if steps:
        lines.append("  along the way")
        lines += [f"     {line}" for line in data_flow(board, steps)]
    return "\n".join(lines)


def _step(step: TraceStep) -> str:
    """A step, and how it came out if the situation said.

    Only a Check is ever answered, so an event or an action prints bare rather
    than being given an answer it never gave.
    """
    return step.key if step.outcome is None else f"{step.key} ={step.outcome!r}"


def _arrow(board: Board, key: str | None) -> str:
    """One named arrow, looked up by the key the walk recorded taking."""
    for edge in board.edges:
        if edge.key == key:
            on = f" on {'/'.join(edge.on_outcomes)}" if edge.on_outcomes else ""
            relation = f" ({edge.relation})" if edge.relation != "normal" else ""
            return f"──{edge.key}{on}{relation}──▶"
    # An edge deleted between the walk and the render. Rare, and saying nothing
    # about which arrow it was beats naming one that is gone.
    return "──▶"


def _refs(references: dict[str, tuple[FieldRef, ...]], names: Sequence[str]) -> str:
    return ", ".join(sorted({str(ref) for name in names for ref in references.get(name, ())}))


__all__ = ["data_flow", "describe", "evidence_for", "path", "reproduces", "walk"]
