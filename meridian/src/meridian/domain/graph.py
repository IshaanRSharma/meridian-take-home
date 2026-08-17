"""The board: cards, the edges between them, and traversal over both.

Two kinds of node. Event, Action and Check are **steps** — they are connected by
edges and together they are the state machine. An Entity is a **thing**: it is
referenced by the steps that read or produce it, and it is never traversed.
Everything here that walks the graph walks steps only.

The graph is deliberately not a DAG. A ``repeat`` edge sends execution back to
work that already ran — a corrected certificate arriving on Thursday re-runs a
check that already passed on Tuesday — so traversal has to terminate by bounding
iterations rather than by assuming acyclicity.

This module states facts about a board. Judgements about whether a board is
*ready* belong to the compiler, the reviewer and the healing loop, each of which
has its own policy.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from meridian.domain.errors import IncompleteError, NotFoundError
from meridian.domain.primitives import (
    ActionConfig,
    BoardKey,
    CheckConfig,
    DomainModel,
    EntityConfig,
    EventConfig,
    FieldRef,
    Finding,
    Outcome,
    Severity,
)

# A dry run walks a finite graph, but `repeat` edges make it cyclic. The bound is
# generous enough that no honest board hits it and small enough to fail fast.
MAX_DRY_RUN_STEPS = 200


# Every card answers the same four questions, and each type answers them from
# its own config. Spelling that out per type is what makes a wrong attribute a
# type error rather than a permanently empty answer nothing would notice.


class EventPrimitive(DomainModel):
    """A card representing something that happens."""

    primitive_type: Literal["event"] = "event"
    key: BoardKey
    group_key: BoardKey | None = None
    config: EventConfig = EventConfig()

    def declared_outcomes(self) -> tuple[Outcome, ...]:
        """Every outcome this card names."""
        return self.config.outcomes

    def declared_inputs(self) -> tuple[str, ...]:
        """Entity keys this card takes as input, of which an Event has none."""
        return ()

    def reads(self) -> tuple[str, ...]:
        """Entity keys this card reads."""
        return self.config.captures

    def produces(self) -> tuple[str, ...]:
        """Entity keys this card brings into existence."""
        # An entity arrives with the event, so capturing it is both reading it
        # and putting it on the board.
        return self.config.captures


class ActionPrimitive(DomainModel):
    """A card representing work that gets done."""

    primitive_type: Literal["action"] = "action"
    key: BoardKey
    group_key: BoardKey | None = None
    config: ActionConfig = ActionConfig()

    def declared_outcomes(self) -> tuple[Outcome, ...]:
        """Every outcome this card names."""
        return self.config.outcomes

    def declared_inputs(self) -> tuple[str, ...]:
        """Entity keys this card takes as input."""
        return self.config.inputs

    def reads(self) -> tuple[str, ...]:
        """Entity keys this card reads."""
        return self.config.inputs

    def produces(self) -> tuple[str, ...]:
        """Entity keys this card brings into existence, which a lookup names."""
        return () if self.config.produces is None else (self.config.produces,)


class CheckPrimitive(DomainModel):
    """A card representing something that must be determined."""

    primitive_type: Literal["check"] = "check"
    key: BoardKey
    group_key: BoardKey | None = None
    config: CheckConfig = CheckConfig()

    def declared_outcomes(self) -> tuple[Outcome, ...]:
        """Every outcome this card names."""
        return self.config.outcomes

    def declared_inputs(self) -> tuple[str, ...]:
        """Entity keys this card takes as input."""
        return self.config.inputs

    def reads(self) -> tuple[str, ...]:
        """Entity keys this card reads."""
        return self.config.inputs

    def produces(self) -> tuple[str, ...]:
        """Entity keys this card brings into existence, of which a Check has none."""
        return ()


class EntityPrimitive(DomainModel):
    """A card representing something the process reads or produces."""

    primitive_type: Literal["entity"] = "entity"
    key: BoardKey
    group_key: BoardKey | None = None
    config: EntityConfig = EntityConfig()

    def declared_outcomes(self) -> tuple[Outcome, ...]:
        """Every outcome this card names, of which an Entity has none."""
        return ()

    def declared_inputs(self) -> tuple[str, ...]:
        """Entity keys this card takes as input, of which an Entity has none."""
        return ()

    def reads(self) -> tuple[str, ...]:
        """Entity keys this card reads, of which an Entity reads none."""
        return ()

    def produces(self) -> tuple[str, ...]:
        """Entity keys this card brings into existence, of which an Entity has none."""
        return ()


FlowNode = EventPrimitive | ActionPrimitive | CheckPrimitive
Primitive = Annotated[
    EventPrimitive | ActionPrimitive | CheckPrimitive | EntityPrimitive,
    Field(discriminator="primitive_type"),
]


class Edge(DomainModel):
    """A transition between two steps.

    ``on_outcomes`` may name several, because one drawn arrow can legitimately
    carry two ways a check came out. An empty list means the transition is
    unconditional, which is only meaningful leaving an Event or an Action.
    """

    key: BoardKey
    from_key: BoardKey
    to_key: BoardKey
    relation: Literal["normal", "exception", "repeat"] = "normal"
    on_outcomes: tuple[str, ...] = ()
    condition: str | None = None

    @model_validator(mode="after")
    def _reject_a_self_edge_that_is_not_a_repeat(self) -> Edge:
        if self.from_key == self.to_key and self.relation != "repeat":
            msg = f"edge {self.key!r} points at itself but is not a repeat"
            raise ValueError(msg)
        return self


class OutcomeWiring(DomainModel):
    """One declared outcome and where, if anywhere, it goes."""

    name: str
    wired: bool
    to: tuple[BoardKey, ...] = ()


class BoardFinding(DomainModel):
    """A finding with the element it belongs to.

    ``anchor`` uses the same vocabulary as thread anchors — ``primitive:<key>``,
    ``edge:<key>``, ``board`` — so a lint finding and a review comment can point
    at the same element.

    ``kind`` says where the fix lives, which is the difference between the two
    surfaces a process owner works on. ``field`` means there is a blank in the
    inspector that closes it — pick a role, tick some fields. ``structure``
    means the fix is on the canvas — draw the missing line, mark the card as an
    ending. Without it the interface has to guess by checking whether ``field``
    happens to name a real config attribute, and half of these do not.
    """

    anchor: str
    field: str
    reason: str
    # Required, not defaulted. A default silently makes every rule added later a
    # freeze gate that nobody chose, and `blocking` has to keep meaning "no
    # correct agent can be generated without this" — grep the literal and you
    # have every gate in the system.
    severity: Severity
    kind: Literal["field", "structure"] = "field"


class TraceStep(DomainModel):
    """One step of a dry run."""

    seq: int
    key: BoardKey
    outcome: str | None = None
    note: str | None = None


class DryRunResult(DomainModel):
    """What happened when a scenario was walked over the board.

    ``unreached`` is the field that earns this: a scenario that dead-ends names
    every step it never got to, which is how "a failing invoice means the COA
    check never runs" becomes visible without anyone having predicted it.
    """

    result: Literal["reached_terminal", "dead_end", "undefined_branch", "loop"]
    trace: tuple[TraceStep, ...] = ()
    unreached: tuple[BoardKey, ...] = ()


class Board(DomainModel):
    """A whole board: its cards, its edges, and where the steps sit on screen.

    ``layout`` holds positions for steps only. An entity has no entry, and that
    absence is the entire implementation of "first-class but not drawn".
    """

    id: UUID | None = None
    name: str
    status: Literal["draft", "in_review", "submitted"] = "draft"
    review_round: int = 0
    primitives: tuple[Primitive, ...] = ()
    edges: tuple[Edge, ...] = ()
    layout: dict[str, dict[str, float]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _reject_duplicate_keys(self) -> Board:
        for label, keys in (
            ("primitive", [p.key for p in self.primitives]),
            ("edge", [e.key for e in self.edges]),
        ):
            duplicates = sorted({k for k in keys if keys.count(k) > 1})
            if duplicates:
                msg = f"duplicate {label} keys: {', '.join(duplicates)}"
                raise ValueError(msg)
        return self

    # --- lookup ------------------------------------------------------------

    def p(self, key: str) -> Primitive:
        """The card with this key."""
        for primitive in self.primitives:
            if primitive.key == key:
                return primitive
        raise NotFoundError(key)

    def has(self, key: str) -> bool:
        """Whether any card carries this key."""
        return any(p.key == key for p in self.primitives)

    def nodes(self) -> tuple[FlowNode, ...]:
        """Every card that participates in the state machine."""
        return tuple(p for p in self.primitives if not isinstance(p, EntityPrimitive))

    def entities(self) -> tuple[EntityPrimitive, ...]:
        """Every card that is read or produced rather than executed."""
        return tuple(p for p in self.primitives if isinstance(p, EntityPrimitive))

    def events(self) -> tuple[EventPrimitive, ...]:
        """Every Event card."""
        return tuple(p for p in self.primitives if isinstance(p, EventPrimitive))

    def actions(self) -> tuple[ActionPrimitive, ...]:
        """Every Action card."""
        return tuple(p for p in self.primitives if isinstance(p, ActionPrimitive))

    def checks(self) -> tuple[CheckPrimitive, ...]:
        """Every Check card."""
        return tuple(p for p in self.primitives if isinstance(p, CheckPrimitive))

    # --- edges -------------------------------------------------------------

    def outgoing(self, key: str) -> tuple[Edge, ...]:
        """Edges leaving this node."""
        return tuple(e for e in self.edges if e.from_key == key)

    def incoming(self, key: str) -> tuple[Edge, ...]:
        """Edges arriving at this node."""
        return tuple(e for e in self.edges if e.to_key == key)

    def terminals(self) -> tuple[FlowNode, ...]:
        """Steps with nowhere to go next."""
        return tuple(s for s in self.nodes() if not self.outgoing(s.key))

    # --- reachability ------------------------------------------------------

    def reachable_from_events(self) -> frozenset[str]:
        """Every step an Event can reach, entities excluded.

        Breadth-first over a graph that may contain cycles, so the visited set is
        what makes it terminate rather than an assumption about edge direction.
        """
        node_keys = {s.key for s in self.nodes()}
        seen: set[str] = set()
        frontier = [e.key for e in self.events()]
        while frontier:
            key = frontier.pop()
            if key in seen or key not in node_keys:
                continue
            seen.add(key)
            frontier.extend(edge.to_key for edge in self.outgoing(key))
        return frozenset(seen)

    def unreachable(self) -> tuple[FlowNode, ...]:
        """Steps no Event can reach."""
        reached = self.reachable_from_events()
        return tuple(s for s in self.nodes() if s.key not in reached)

    def downstream(self, key: str) -> frozenset[str]:
        """Every step reachable from this one, excluding itself unless a cycle returns."""
        seen: set[str] = set()
        frontier = [edge.to_key for edge in self.outgoing(key)]
        while frontier:
            nxt = frontier.pop()
            if nxt in seen:
                continue
            seen.add(nxt)
            frontier.extend(edge.to_key for edge in self.outgoing(nxt))
        return frozenset(seen)

    def upstream(self, key: str) -> frozenset[str]:
        """Every step that can reach this one.

        Transitive, because a capability needing a field asks whether *anything*
        before it supplies that field, not just its immediate predecessor.
        """
        seen: set[str] = set()
        frontier = [edge.from_key for edge in self.incoming(key)]
        while frontier:
            prev = frontier.pop()
            if prev in seen:
                continue
            seen.add(prev)
            frontier.extend(edge.from_key for edge in self.incoming(prev))
        return frozenset(seen)

    # --- outcomes ----------------------------------------------------------

    def outcomes(self, key: str) -> tuple[OutcomeWiring, ...]:
        """Every outcome this card declares, and where each one goes.

        An unwired outcome is the most common real gap on a board, so it is
        reported as a fact rather than left to be derived from edges.
        """
        wiring = []
        for outcome in self.p(key).declared_outcomes():
            targets = tuple(e.to_key for e in self.outgoing(key) if outcome.name in e.on_outcomes)
            wiring.append(OutcomeWiring(name=outcome.name, wired=bool(targets), to=targets))
        return tuple(wiring)

    # --- entities ----------------------------------------------------------

    def readers_of(self, entity_key: str) -> tuple[FlowNode, ...]:
        """Steps that read this entity, whether by capturing it or taking it as input."""
        return tuple(node for node in self.nodes() if entity_key in node.reads())

    def producers_of(self, entity_key: str) -> tuple[FlowNode, ...]:
        """Steps that bring this entity into existence."""
        return tuple(node for node in self.nodes() if entity_key in node.produces())

    def entity_has_field(self, ref_entity: str, path: str) -> bool:
        """Whether a declared entity carries this field path.

        Compares against the top-level keys of the entity's schema, which is as
        deep as a board-level check needs to go — an unknown root is a genuine
        mistake, while a wrong leaf inside a nested object is caught by
        extraction against the schema.
        """
        if not self.has(ref_entity):
            return False
        entity = self.p(ref_entity)
        if not isinstance(entity, EntityPrimitive):
            return False
        root = path.split(".", 1)[0].removesuffix("[]")
        return root in entity.config.fields

    # --- references ---------------------------------------------------------

    def field_references(self, card: FlowNode) -> dict[str, tuple[FieldRef, ...]]:
        """Field references this card makes, grouped by the config field holding them.

        Reported per field so a finding can point at `criteria` or `evidence`
        rather than at the card as a whole.
        """
        config = card.config
        refs: dict[str, tuple[FieldRef, ...]] = {}
        if isinstance(config, EventConfig) and config.correlation_key is not None:
            refs["correlation_key"] = (config.correlation_key,)
        if isinstance(config, ActionConfig):
            refs["payload_fields"] = config.payload_fields
        if isinstance(config, CheckConfig):
            refs["evidence"] = config.evidence
            criteria: list[FieldRef] = []
            for criterion in config.criteria:
                criteria.extend(criterion.references())
            refs["criteria"] = tuple(criteria)
        return {name: value for name, value in refs.items() if value}

    # --- dry run -----------------------------------------------------------

    def dry_run(
        self, outcomes: Mapping[str, str | Sequence[str]], start: str | None = None
    ) -> DryRunResult:
        """Walk one scenario, where ``outcomes`` picks how each Check comes out.

        An answer is either one string, meaning the card always comes out that
        way, or a sequence answering each visit in turn. The sequence is what
        makes a resubmission expressible: a form that is incomplete on Tuesday
        and complete on Thursday is one card visited twice, and with a single
        answer per card that walk could only ever be reported as a loop —
        indistinguishable from a process that never terminates. Which is to say
        the review loop would stop being able to prove a resolution exactly
        when the answer "it comes back once corrected" drew the repeat edge.

        ``Mapping``, not ``dict``, because ``dict`` is invariant in its value
        type: a caller holding a plain ``dict[str, str]`` would otherwise stop
        type-checking against a signature that also admits sequences.

        Deliberately dumb — it is the one place this system interprets rather
        than compiles, and its job is to say whether a path exists, not whether
        the path is right. Only real data catches a scenario that reaches the
        wrong terminal.
        """
        entries = [e.key for e in self.events()]
        if start is None and len(entries) > 1:
            # Picking one entry point silently answers a question the caller
            # never asked, and the other events are simply dropped.
            msg = (
                f"this board has {len(entries)} events ({', '.join(entries)}); "
                "pass start to say which one this scenario begins at"
            )
            raise IncompleteError(msg)
        current = start or (entries[0] if entries else None)
        if current is None:
            return DryRunResult(result="dead_end", unreached=tuple(s.key for s in self.nodes()))

        trace: list[TraceStep] = []
        visited: set[str] = set()
        visits: Counter[str] = Counter()
        result: Literal["reached_terminal", "dead_end", "undefined_branch", "loop"] = "loop"

        for seq in range(1, MAX_DRY_RUN_STEPS + 1):
            node = self.p(current)
            visited.add(current)
            outcome = _answer(outcomes.get(current), visits[current])
            visits[current] += 1
            leaving = self.outgoing(current)

            if not leaving:
                terminal = isinstance(node, ActionPrimitive) and node.config.is_terminal
                trace.append(
                    TraceStep(
                        seq=seq,
                        key=current,
                        outcome=outcome,
                        note=None if terminal else "no outgoing edges",
                    )
                )
                result = "reached_terminal" if terminal else "dead_end"
                break

            taken = _edge_for(leaving, outcome)
            if taken is None:
                trace.append(
                    TraceStep(
                        seq=seq,
                        key=current,
                        outcome=outcome,
                        note=(
                            "no outcome given and no unconditional edge"
                            if outcome is None
                            else f"no edge carries {outcome!r}"
                        ),
                    )
                )
                result = "undefined_branch"
                break

            trace.append(TraceStep(seq=seq, key=current, outcome=outcome))
            current = taken.to_key

        return DryRunResult(
            result=result,
            trace=tuple(trace),
            unreached=tuple(s.key for s in self.nodes() if s.key not in visited),
        )


def _answer(given: str | Sequence[str] | None, visit: int) -> str | None:
    """How a card comes out on its ``visit``-th arrival, counting from zero.

    An exhausted sequence holds its last answer rather than falling silent. A
    resubmission that keeps arriving incomplete has to stay a loop: running out
    of answers is not evidence that the process terminates, and it must not
    become permission to take a different edge.
    """
    if given is None or isinstance(given, str):
        return given
    if not given:
        # A sequence of no answers says nothing about how the card came out,
        # which is exactly the position of a scenario that never named it.
        return None
    return given[min(visit, len(given) - 1)]


def _edge_for(leaving: tuple[Edge, ...], outcome: str | None) -> Edge | None:
    """The edge a scenario takes, preferring one that names the outcome."""
    if outcome is not None:
        for edge in leaving:
            if outcome in edge.on_outcomes:
                return edge
        # A named outcome with no edge naming it is an undefined branch, even
        # when an unconditional edge exists — otherwise an unwired outcome would
        # silently fall through and the gap would never surface.
        return None
    for edge in leaving:
        if not edge.on_outcomes:
            return edge
    # No outcome named means we do not know how the card came out, so every edge
    # left here is conditional on something unknown. Taking the only one would be
    # a guess reported as a traversal.
    return None


__all__ = [
    "MAX_DRY_RUN_STEPS",
    "ActionPrimitive",
    "Board",
    "BoardFinding",
    "CheckPrimitive",
    "DryRunResult",
    "Edge",
    "EntityPrimitive",
    "EventPrimitive",
    "Finding",
    "FlowNode",
    "OutcomeWiring",
    "Primitive",
    "TraceStep",
]
