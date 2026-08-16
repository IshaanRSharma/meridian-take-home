"""Turning conversations into what one card needs to know.

Threads are stored conversation-shaped — one question ranging over several
cards. The spec is primitive-shaped — one card carrying everything settled about
it. The anchor performs that transposition and is then thrown away: nothing in a
frozen spec references an anchor, because codegen reads four lists of English
and a config, and resolving a reference is work it should never have to do.

The chain runs broad to narrow, so a statement made about one card beats one
made about the whole board — the same convention as lexical scope. Which card a
statement reaches is what stops a rule about invoice batch numbers appearing in
a file that only ever sees certificates.

Statements are **inlined, not referenced**. A board-level rule physically
repeats on every card that inherits it. That duplication is the trade: the
payload is larger and the consumer never joins anything.
"""

from meridian.domain.frozen import ScopedContext
from meridian.domain.graph import Board, Primitive
from meridian.domain.review import Anchor, Assertion

# Broad to narrow. Narrower wins on conflict, which is why the order matters
# rather than being alphabetical.
_BREADTH: dict[str, int] = {"board": 0, "group": 1, "entity_field": 2, "primitive": 3}


def reaches(card: Primitive, anchor: Anchor) -> bool:
    """Whether something said about this anchor bears on this card.

    An ``entity_field`` statement travels sideways rather than down: it reaches
    any card that reads the entity, which is how a constraint on a field lands
    on the checks that test it and nowhere else.
    """
    match anchor.kind:
        case "board":
            return True
        case "group":
            return card.group_key is not None and card.group_key == anchor.key
        case "primitive":
            return card.key == anchor.key
        case "entity_field":
            entity = (anchor.key or "").split(".", 1)[0]
            return entity in card.reads()
        case _:
            # An edge statement belongs to the edge. Attaching it to the cards
            # on either end would put a claim about a transition inside a file
            # that only knows about a step.
            return False


def context_for(board: Board, assertions: tuple[Assertion, ...], key: str) -> ScopedContext:
    """Everything settled that bears on one card.

    Superseded statements are dropped: they stay in the database for the audit
    trail, and a spec carries only what is currently true.
    """
    card = board.p(key)
    relevant = [a for a in assertions if a.is_active() and reaches(card, a.anchor)]

    inherited: list[str] = []
    local: list[str] = []
    negative: list[str] = []

    for assertion in sorted(relevant, key=_ordering):
        if assertion.kind == "negative":
            # Collected at every level and never overridden — it states
            # something about the world, not about a step, so it does not stop
            # being true lower down.
            negative.append(assertion.statement)
        elif assertion.anchor.kind == "primitive":
            local.append(f"[{assertion.kind}] {assertion.statement}")
        else:
            inherited.append(f"[{assertion.kind}] {assertion.statement}")

    return ScopedContext(
        inherited=tuple(inherited),
        local=tuple(local),
        negative=tuple(negative),
        provenance=tuple(sorted({str(a.thread_id) for a in relevant if a.thread_id})),
    )


def _ordering(assertion: Assertion) -> tuple[int, str, str]:
    """Broad first, then stable. Two freezes of one board must agree exactly."""
    return (
        _BREADTH.get(assertion.anchor.kind, 99),
        assertion.kind,
        assertion.statement,
    )
