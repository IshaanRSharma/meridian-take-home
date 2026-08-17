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

from collections.abc import Callable
from typing import Any

from meridian.compiler import decisions as decision_sweeps
from meridian.compiler import rules
from meridian.compiler.context import context_for
from meridian.domain.frozen import FrozenSpec, ScopedContext, SpecPrimitive
from meridian.domain.graph import ActionPrimitive, Board, EventPrimitive, FlowNode
from meridian.domain.keys import key_for
from meridian.domain.review import Assertion, Thread


def review_payload(
    board: Board,
    threads: tuple[Thread, ...] = (),
    assertions: tuple[Assertion, ...] = (),
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
                # The whole config, not a summary of it. Structure is what lint
                # and the sweeps already interrogate; every *semantic* question
                # comes from the prose the owner typed — the subject lines they
                # quoted, the operators they picked, the aside in `instructions`
                # — and none of that is derivable from the graph.
                "config": card.config.model_dump(mode="json", exclude_none=True),
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
                # The whole schema, like the steps get. A flat list of top-level
                # names hides that a line item is a repeated thing with fields of
                # its own, that every identifier on it is nullable on purpose so
                # a check can detect the failure it exists to detect, and the
                # note the owner wrote about how to read the document.
                **entity.config.model_dump(mode="json", exclude_none=True),
                "read_by": [c.key for c in board.readers_of(entity.key)],
                "produced_by": [c.key for c in board.producers_of(entity.key)],
            }
            for entity in board.entities()
        ],
        # Every field the board declares that no step ever looks at. Each is
        # either a rule nobody wrote down or a field that should not be there,
        # and a reviewer cannot ask about either without being told which.
        "never_read": _never_read(board),
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
        # What every conversation so far actually concluded, flat. `prior_threads`
        # says what was ASKED, and the answer is buried in a turn the model has to
        # find and interpret; a settled statement says what was DECIDED, in one
        # line, in the owner's own words. Flat rather than inlined per card the way
        # the spec does it: codegen reads one entry and joins nothing, but a
        # reviewer joins fine, and inlining would repeat a board-level rule on
        # every card while dropping the edge and entity statements it most needs.
        "settled": [
            {
                "anchor": str(assertion.anchor),
                "kind": assertion.kind,
                "statement": assertion.statement,
            }
            for assertion in assertions
            if assertion.is_active()
        ],
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
                # What it is recognised by. The model is asked not to repeat a
                # question and has to be able to tell which is which.
                "decision_key": thread.decision_key,
                "scenario_key": thread.scenario_key,
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


def _never_read(board: Board) -> list[str]:
    """Fields the board declares and no step references."""
    referenced = {
        f"{ref.entity}.{ref.path}"
        for card in board.nodes()
        for refs in board.field_references(card).values()
        for ref in refs
    }
    return sorted(
        full
        for entity in board.entities()
        for field in entity.config.fields
        if not any(seen.startswith(full := f"{entity.key}.{field}") for seen in referenced)
    )


def _turns(thread: Thread) -> tuple[Any, ...]:
    return tuple(sorted(thread.messages, key=lambda m: m.seq))


def spec_payload(
    board: Board,
    assertions: tuple[Assertion, ...] = (),
    version: int = 1,
    slug: str = "",
) -> FrozenSpec:
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
        version=version,
        board_id=board.id,
        name=board.name,
        slug=slug or key_for(board.name, (), fallback="agent"),
        entities={e.key: e.config for e in board.entities()},
        primitives=primitives,
        edges=board.edges,
        # A statement about a transition reaches no card — putting it in a
        # step's file would tell that step about something it does not do — so
        # it lives here or it is lost at the freeze.
        edge_context=_edge_context(board, assertions),
        # Same reason as the line above, for the other half of the board: a
        # statement about a document reaches no step either.
        entity_context=_entity_context(board, assertions),
        capabilities=tuple(sorted({c for p in primitives.values() for c in p.capabilities})),
    ).sealed()


def capabilities_for(card: FlowNode) -> tuple[str, ...]:
    """What this card needs from outside itself.

    A Check never appears here. It reads data that has already arrived and
    returns a result, which is what lets it be workflow code rather than an
    activity, and what makes a failing eval case fail for a logic reason.

    Every name is **derived** from a closed enum the process owner chose, never
    looked up in a table beside one. A table has to be kept in step with the
    enum and nothing enforces that, so the first new channel someone adds either
    raises here or silently produces a card that reaches the world with no
    declared capability.

    Reading a document is deliberately absent. The generator already has the
    entity and its field schema; how those fields come out of whatever actually
    arrived — parse a body, read a PDF — is an implementation decision, and
    implementation decisions are the ones it is allowed to make. Nothing on a
    board says whether a thing is a file, so a capability claiming it would be
    this compiler guessing, and it guessed wrong on every process without
    documents.

    Never derived from `system`, which is free text and per-customer. That stays
    on the config for the bindings file to key on, so the same spec deploys to a
    company running different software.
    """
    needed: set[str] = set()

    if isinstance(card, EventPrimitive) and card.config.channel:
        needed.add(f"{card.config.channel}.fetch")

    if isinstance(card, ActionPrimitive):
        # Both effects that put a person on the other end have to reach them,
        # and both do it the same way. A decision nobody is asked for is not a
        # decision — without this an engineer reading the capability list would
        # never learn a mailbox is involved.
        if card.config.effect in ("notify", "decide") and card.config.channel:
            needed.add(f"{card.config.channel}.send")

        match card.config.effect:
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
    return _context_by_key(
        {edge.key for edge in board.edges},
        assertions,
        lambda a: a.anchor.key if a.anchor.kind == "edge" else None,
    )


def _entity_context(board: Board, assertions: tuple[Assertion, ...]) -> dict[str, ScopedContext]:
    """What was settled about the things a process reads, rather than its steps.

    The transposition walks steps, so a statement anchored on an entity reached
    no card and was dropped at the freeze without a word — and those are exactly
    the statements a generator writing an extraction schema needs. *How do you
    recognise a certificate among the attachments* and *batch numbers are seven
    digits after the prefix* are facts about the document, true wherever it is
    read, so they belong to it rather than to whichever step read it first.

    Field-level statements land here too. `entity_field:invoice.batch_no` still
    travels sideways to every step reading that invoice, because a check needs
    to know how to compare — but it is also a fact about the invoice itself.
    """
    entities = {entity.key for entity in board.entities()}

    def owner(assertion: Assertion) -> str | None:
        anchor = assertion.anchor
        if anchor.kind == "primitive":
            return anchor.key
        if anchor.kind == "entity_field" and anchor.key:
            return anchor.key.split(".", 1)[0]
        return None

    return _context_by_key(entities, assertions, owner)


def _context_by_key(
    keys: set[str],
    assertions: tuple[Assertion, ...],
    owner: Callable[[Assertion], str | None],
) -> dict[str, ScopedContext]:
    """Group settled statements under whichever element ``owner`` assigns them."""
    grouped: dict[str, list[Assertion]] = {}
    for assertion in assertions:
        key = owner(assertion) if assertion.is_active() else None
        if key is not None and key in keys:
            grouped.setdefault(key, []).append(assertion)

    return {
        key: ScopedContext(
            local=tuple(
                f"[{a.kind}] {a.statement}"
                for a in sorted(found, key=lambda a: (a.kind, a.statement))
                if a.kind != "negative"
            ),
            negative=tuple(a.statement for a in found if a.kind == "negative"),
            provenance=tuple(sorted({str(a.thread_id) for a in found if a.thread_id})),
        )
        for key, found in grouped.items()
    }
