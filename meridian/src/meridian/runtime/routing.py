"""Which step runs next, from the edge table.

The board is a state machine rather than a DAG — a ``repeat`` edge sends the
process back to work that already ran, which is what a corrected document
arriving on Thursday requires. Nothing here treats a cycle as special; routing
backwards is an ordinary next step.

Pure, and deliberately outside ``temporal/``: deciding where to go next is graph
traversal, not durable execution, so it stays testable with nothing installed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


class AmbiguousRouteError(Exception):
    """Two edges claim the same outcome from the same step.

    Raised rather than picking one. Arbitrary behaviour would mean the same
    input takes different paths depending on edge ordering, and a failing eval
    case could not be reproduced — which is worse than stopping.
    """


@dataclass(frozen=True)
class Edge:
    """One transition. Empty ``on_outcomes`` means unconditional."""

    key: str
    from_key: str
    to_key: str
    on_outcomes: tuple[str, ...] = ()


class Routes:
    """The transition relation for one board."""

    def __init__(self, edges: Iterable[Edge]) -> None:
        """Group edges by their source step, preserving declaration order."""
        self._by_step: dict[str, list[Edge]] = {}
        for edge in edges:
            self._by_step.setdefault(edge.from_key, []).append(edge)

    @classmethod
    def from_edges(cls, rows: Iterable[tuple[str, str, str, tuple[str, ...]]]) -> Routes:
        """Build from ``(key, from, to, on_outcomes)`` tuples, as the spec stores them."""
        return cls(Edge(*row) for row in rows)

    def next(self, step: str, outcome: str) -> str | None:
        """The step that follows, or ``None`` if the process stops here.

        ``None`` covers two different situations on purpose. A terminal step has
        no outgoing edges at all. An outcome nobody wired also lands here — the
        compiler reports that board as incomplete, but an agent built from an
        earlier spec still has to stop cleanly rather than fail mid-case, and the
        trace records where it stopped.
        """
        matches = [e for e in self._by_step.get(step, ()) if self._matches(e, outcome)]
        if not matches:
            return None
        if len(matches) > 1:
            keys = ", ".join(e.key for e in matches)
            msg = f"{step!r} has {len(matches)} edges for outcome {outcome!r}: {keys}"
            raise AmbiguousRouteError(msg)
        return matches[0].to_key

    def is_terminal(self, step: str) -> bool:
        """Whether anything leaves this step at all."""
        return not self._by_step.get(step)

    @staticmethod
    def _matches(edge: Edge, outcome: str) -> bool:
        # An Event has no outcomes to branch on, so its edge carries none and
        # fires whatever happened.
        return not edge.on_outcomes or outcome in edge.on_outcomes
