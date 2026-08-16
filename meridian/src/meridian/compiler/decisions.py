"""The decisions a drawing makes.

A lint rule says *something is missing* — provable, and it cannot be wrong. This
says *the drawing made a choice*, and the choice might be wrong. Nothing here is
absent, nothing contradicts, lint is silent: what these surface is a decision
nobody has examined.

Six sweeps over structural properties, not six named patterns. Each one asks the
same question of a different feature — **could this plausibly have been drawn
otherwise?** — so a pattern nobody anticipated tends to fall out of a sweep that
already exists rather than needing new code.

Every claim is **derived from the graph**, never authored. A reviewer chooses
which decisions are worth raising and phrases them in the process owner's words,
but it cannot assert a pattern the board does not contain, because it never
writes the claim in the first place.
"""

from meridian.domain.graph import Board, CheckPrimitive, Edge, FlowNode
from meridian.domain.primitives import DomainModel

Kind = str


class DecisionPoint(DomainModel):
    """One choice the drawing makes, and what it could have been instead.

    ``elements`` is sorted, which makes ``key()`` stable: the same board yields
    the same identity every run, so a question asked in round one is not asked
    again in round three. Dedup is exact rather than a guess at whether two
    sentences mean the same thing.
    """

    kind: Kind
    elements: tuple[str, ...]
    claim: str
    alternative: str

    def key(self) -> str:
        """A stable identity, derived from the graph rather than from wording."""
        return f"{self.kind}:{'|'.join(sorted(self.elements))}"


def _named(card: FlowNode) -> str:
    """What the process owner called this card."""
    return card.config.name or card.key


def sequence(board: Board) -> list[DecisionPoint]:
    """One check gating another.

    The drawing says the second only happens when the first came out a
    particular way. That is a real decision — the alternative, running both and
    reporting everything at once, is equally reasonable and produces a different
    agent.
    """
    found: list[DecisionPoint] = []
    for edge in board.edges:
        if not edge.on_outcomes or not board.has(edge.from_key) or not board.has(edge.to_key):
            continue
        before, after = board.p(edge.from_key), board.p(edge.to_key)
        if not isinstance(before, CheckPrimitive) or not isinstance(after, CheckPrimitive):
            continue
        outcomes = " or ".join(repr(o) for o in edge.on_outcomes)
        found.append(
            DecisionPoint(
                kind="sequence",
                elements=(f"primitive:{before.key}", f"edge:{edge.key}", f"primitive:{after.key}"),
                claim=f"{_named(after)} only runs when {_named(before)} comes out {outcomes}",
                alternative=f"{_named(before)} and {_named(after)} both run, "
                "and everything wrong is reported together",
            )
        )
    return found


def termination(board: Board) -> list[DecisionPoint]:
    """Places the drawing says the process stops.

    A failure path that leaves and never comes back is the highest-value one:
    the process owner may well answer that the shipment returns once corrected,
    and that answer adds a repeat edge — which is where the board stops being
    acyclic.
    """
    found: list[DecisionPoint] = []
    for card in board.terminals():
        inbound = board.incoming(card.key)
        if not inbound:
            continue  # unreachable is lint's problem, not a decision
        by_exception = any(e.relation == "exception" for e in inbound)
        elements = (f"primitive:{card.key}", *(f"edge:{e.key}" for e in inbound))
        found.append(
            DecisionPoint(
                kind="termination",
                elements=elements,
                claim=f"the process ends at {_named(card)}"
                + (" whenever something is wrong" if by_exception else ""),
                alternative="the shipment comes back once the problem is fixed"
                if by_exception
                else "something happens after this",
            )
        )
    return found


def convergence(board: Board) -> list[DecisionPoint]:
    """Places two different situations get the same treatment."""
    found: list[DecisionPoint] = []
    for card in board.nodes():
        inbound = board.incoming(card.key)
        if len(inbound) < 2:  # noqa: PLR2004 - two is what makes it a convergence
            continue
        found.append(
            DecisionPoint(
                kind="convergence",
                elements=(f"primitive:{card.key}", *(f"edge:{e.key}" for e in inbound)),
                claim=f"{_describe_arrivals(board, inbound)} all lead to {_named(card)}",
                alternative="each one is handled differently",
            )
        )
    return found


def boundedness(board: Board) -> list[DecisionPoint]:
    """Waiting with no end to it."""
    found: list[DecisionPoint] = []
    for event in board.events():
        timing = event.config.timing
        if timing is None or timing.deadline:
            continue
        found.append(
            DecisionPoint(
                kind="boundedness",
                elements=(f"primitive:{event.key}",),
                claim=f"the process waits for {_named(event)} indefinitely",
                alternative="it gives up, or escalates, after some time",
            )
        )
    for edge in board.edges:
        if edge.relation == "repeat":
            found.append(
                DecisionPoint(
                    kind="boundedness",
                    elements=(f"edge:{edge.key}",),
                    claim="this can go round any number of times",
                    alternative="there is a point where it stops trying",
                )
            )
    return found


def entry(board: Board) -> list[DecisionPoint]:
    """How information gets into the process at all."""
    events = board.events()
    if not events:
        return []
    names = " and ".join(_named(e) for e in events)
    return [
        DecisionPoint(
            kind="entry",
            elements=tuple(f"primitive:{e.key}" for e in events),
            claim=f"information reaches this process only through {names}",
            alternative="something else can start or change it",
        )
    ]


def coverage(board: Board) -> list[DecisionPoint]:
    """Endings a shipment can reach without a given check ever running.

    Distinct from `sequence`, which is about one edge. This is about the whole
    path: it catches a check that is skippable by some route even when no single
    edge looks wrong.
    """
    found: list[DecisionPoint] = []
    checks = board.checks()
    for terminal in board.terminals():
        for check in checks:
            if terminal.key == check.key:
                continue
            if terminal.key in _reachable_without(board, check.key):
                found.append(
                    DecisionPoint(
                        kind="coverage",
                        elements=(f"primitive:{terminal.key}", f"primitive:{check.key}"),
                        claim=f"a shipment can reach {_named(terminal)} "
                        f"without {_named(check)} ever running",
                        alternative=f"{_named(check)} always runs first",
                    )
                )
    return found


SWEEPS = (sequence, termination, convergence, boundedness, entry, coverage)


def decisions(board: Board) -> list[DecisionPoint]:
    """Every decision the drawing makes, in a stable order."""
    found = [point for sweep in SWEEPS for point in sweep(board)]
    return sorted(found, key=lambda d: d.key())


def _reachable_without(board: Board, skipped: str) -> frozenset[str]:
    """Steps an event can still reach if one card is taken out of the graph."""
    node_keys = {n.key for n in board.nodes()} - {skipped}
    seen: set[str] = set()
    frontier = [e.key for e in board.events() if e.key != skipped]
    while frontier:
        key = frontier.pop()
        if key in seen or key not in node_keys:
            continue
        seen.add(key)
        frontier.extend(edge.to_key for edge in board.outgoing(key))
    return frozenset(seen)


def _describe_arrivals(board: Board, inbound: tuple[Edge, ...]) -> str:
    """What the incoming edges represent, in the process owner's words."""
    labels: list[str] = []
    for edge in inbound:
        if edge.on_outcomes:
            labels.extend(edge.on_outcomes)
        elif board.has(edge.from_key):
            source = board.p(edge.from_key)
            labels.append(source.config.name or source.key)
    return " and ".join(repr(label) for label in labels) or "several paths"


__all__ = [
    "SWEEPS",
    "DecisionPoint",
    "boundedness",
    "convergence",
    "coverage",
    "decisions",
    "entry",
    "sequence",
    "termination",
]
