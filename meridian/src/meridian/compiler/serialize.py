"""Two serialisations of one board, for two very different readers.

Neither is stored. The board is rows; only the frozen spec is written.

The **reviewer payload** goes to a model that has to ask a good question, so it
carries everything observable — the cards, the wiring, what lint found, what
decisions the drawing makes, and every conversation already had. Prior threads
arrive with all their turns rather than a summary, because the process owner's
own vocabulary and their hedges live in the turns and a distilled statement
loses both.

The **frozen spec** goes to a code generator that must make implementation
decisions and never business ones, so it is defined as much by what it omits:
no questions, no anchors, no tool names, nobody's address. Each card arrives
with its statements already inlined, so codegen reads one entry and joins
nothing.

Two functions rather than one with a mode flag. They share the helpers below and
almost nothing else.
"""

from typing import Any

from meridian.compiler import decisions as decision_sweeps
from meridian.compiler import rules
from meridian.compiler.context import context_for
from meridian.domain.frozen import FrozenSpec, ScopedContext, SpecPrimitive
from meridian.domain.graph import ActionPrimitive, Board, EventPrimitive, FlowNode
from meridian.domain.review import Assertion, Thread

# What a card needs from outside itself, derived from the closed enums a process
# owner chose. Never from `system`, which is free text and per-customer — that
# stays on the config for the bindings file to key on, so the same spec deploys
# to a company running different software.
_NOTIFY = {"email": "email.send", "sms": "sms.send", "phone": "phone.call", "queue": "queue.push"}


def review_payload(
    board: Board,
    threads: tuple[Thread, ...] = (),
    corpus: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything a reviewer needs to ask a good question about this board."""
    payload: dict[str, Any] = {
        "board": {"name": board.name, "status": board.status, "round": board.review_round},
        "nodes": [
            {
                "key": card.key,
                "type": card.primitive_type,
                "name": card.config.name,
                "group": card.group_key,
                "reads": list(card.reads()),
                "in": [e.key for e in board.incoming(card.key)],
                "out": [e.key for e in board.outgoing(card.key)],
            }
            for card in board.nodes()
        ],
        "edges": [
            {
                "key": edge.key,
                "from": edge.from_key,
                "to": edge.to_key,
                "relation": edge.relation,
                "on_outcomes": list(edge.on_outcomes),
            }
            for edge in board.edges
        ],
        "entities": [
            {
                "key": entity.key,
                "name": entity.config.name,
                "identified_by": entity.config.identified_by,
                "cardinality": entity.config.cardinality.model_dump(mode="json"),
                "fields": sorted(entity.config.fields),
                "read_by": [c.key for c in board.readers_of(entity.key)],
                "produced_by": [c.key for c in board.producers_of(entity.key)],
            }
            for entity in board.entities()
        ],
        # Stated rather than left to be derived: an outcome leading nowhere is
        # the most common real gap, and a model should not have to find it by
        # comparing two lists.
        "outcomes": {
            card.key: [w.model_dump(mode="json") for w in board.outcomes(card.key)]
            for card in board.nodes()
            if board.outcomes(card.key)
        },
        "findings": [f.model_dump(mode="json") for f in rules.findings(board)],
        "decisions": [d.model_dump(mode="json") for d in decision_sweeps.decisions(board)],
        # Including rejected ones. A question already dismissed must not come
        # back, and knowing *why* it was dismissed stops a near-miss re-ask.
        "prior_threads": [
            {
                "id": str(thread.id) if thread.id else None,
                "status": thread.status,
                "category": thread.category,
                "round": thread.round,
                "question": thread.question,
                "reason": thread.reason,
                "anchors": [str(a) for a in thread.anchors],
                "messages": [
                    m.model_dump(mode="json", exclude={"created_at"}) for m in _turns(thread)
                ],
            }
            for thread in threads
        ],
    }
    if corpus is not None:
        payload["corpus"] = corpus
    return payload


def _turns(thread: Thread) -> tuple[Any, ...]:
    return tuple(sorted(thread.messages, key=lambda m: m.seq))


def spec_payload(board: Board, assertions: tuple[Assertion, ...] = ()) -> FrozenSpec:
    """The board as a code generator receives it, sealed with its checksum."""
    primitives = {
        card.key: SpecPrimitive(
            key=card.key,
            primitive_type=card.primitive_type,
            config=card.config,
            capabilities=capabilities_for(card),
            context=context_for(board, assertions, card.key),
        )
        for card in board.nodes()
    }

    return FrozenSpec(
        board_id=board.id,
        entities={e.key: e.config for e in board.entities()},
        primitives=primitives,
        edges=board.edges,
        # A statement about a transition reaches no card — putting it in a
        # step's file would tell that step about something it does not do — so
        # it lives here or it is lost at the freeze.
        edge_context=_edge_context(board, assertions),
        capabilities=tuple(sorted({c for p in primitives.values() for c in p.capabilities})),
    ).sealed()


def capabilities_for(card: FlowNode) -> tuple[str, ...]:
    """What this card needs from outside itself.

    A Check never appears here. It reads data that has already arrived and
    returns a result, which is what lets it be workflow code rather than an
    activity, and what makes a failing eval case fail for a logic reason.
    """
    needed: set[str] = set()

    if isinstance(card, EventPrimitive):
        if card.config.channel == "email":
            needed.add("email.fetch")
        if card.config.captures:
            needed.update({"storage.put", "doc.extract"})

    if isinstance(card, ActionPrimitive):
        match card.config.effect:
            case "notify":
                if card.config.channel:
                    needed.add(_NOTIFY[card.config.channel])
            case "record":
                needed.add("system.write")
            case "lookup":
                needed.add("system.read")
            case "decide":
                needed.add("human.decide")
            case _:
                pass

    return tuple(sorted(needed))


def _edge_context(board: Board, assertions: tuple[Assertion, ...]) -> dict[str, ScopedContext]:
    by_edge: dict[str, list[Assertion]] = {}
    for assertion in assertions:
        if assertion.is_active() and assertion.anchor.kind == "edge" and assertion.anchor.key:
            by_edge.setdefault(assertion.anchor.key, []).append(assertion)

    return {
        edge.key: ScopedContext(
            local=tuple(
                f"[{a.kind}] {a.statement}"
                for a in sorted(by_edge[edge.key], key=lambda a: (a.kind, a.statement))
                if a.kind != "negative"
            ),
            negative=tuple(a.statement for a in by_edge[edge.key] if a.kind == "negative"),
            provenance=tuple(sorted({str(a.thread_id) for a in by_edge[edge.key] if a.thread_id})),
        )
        for edge in board.edges
        if edge.key in by_edge
    }
