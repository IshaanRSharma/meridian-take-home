"""What counts as incomplete, and how badly.

These are the compiler's **policy**. A board reports facts — three outcomes and
two edges, this reference does not resolve, nothing leads to that card — and the
rules here decide which of those facts is a problem and how much of one. The
reviewer and the healing loop are free to disagree; that is why the judgement
does not live on ``Board``.

Two things every rule holds to. It reports in the process owner's language,
because ``reason`` is the text rendered on the card — if a rule cannot be phrased
as something a warehouse supervisor would understand, the field it guards is
probably wrong. And it fires only when something is genuinely absent or
contradictory, never merely unusual: a rule that fires on a finished board is
noise, and noise is what makes someone stop reading findings.

Deliberately **not** here: whether a capability resolves to a real tool. A
process owner cannot fix an unbound channel, so surfacing it on the canvas would
be noise they can do nothing about. That is a separate gate with a different
owner, run before codegen rather than before the freeze.
"""

from collections.abc import Callable

from meridian.domain.graph import (
    ActionPrimitive,
    Board,
    BoardFinding,
    FlowNode,
)
from meridian.domain.primitives import Severity

Rule = Callable[[Board], list[BoardFinding]]

SEVERITY_RANK: dict[Severity, int] = {"minor": 0, "important": 1, "blocking": 2}


# --- what each card says about itself --------------------------------------


def card_completeness(board: Board) -> list[BoardFinding]:
    """Fields a card needs that nobody has filled in."""
    return [
        BoardFinding(
            anchor=f"primitive:{p.key}", field=f.field, reason=f.reason, severity=f.severity
        )
        for p in board.primitives
        for f in p.config.findings()
    ]


# --- edges -----------------------------------------------------------------


def edge_endpoints_exist(board: Board) -> list[BoardFinding]:
    """Edges pointing at a card that is not on the board.

    Deleting a card deliberately leaves its edges and its conversations behind,
    so this is a finding rather than a cascade — the process owner decides
    whether the connection or the card was the mistake.
    """
    return [
        BoardFinding(
            anchor=f"edge:{edge.key}",
            field=role,
            reason=f"This connects to {key!r}, which is not on the board.",
            kind="structure",
        )
        for edge in board.edges
        for role, key in (("from_key", edge.from_key), ("to_key", edge.to_key))
        if not board.has(key)
    ]


def edge_endpoints_are_steps(board: Board) -> list[BoardFinding]:
    """Edges pointing at an entity, which is read rather than executed."""
    entity_keys = {e.key for e in board.entities()}
    return [
        BoardFinding(
            anchor=f"edge:{edge.key}",
            field=role,
            reason=f"{key!r} is something the process reads, not a step.",
            kind="structure",
        )
        for edge in board.edges
        for role, key in (("from_key", edge.from_key), ("to_key", edge.to_key))
        if key in entity_keys
    ]


def edge_outcomes_are_declared(board: Board) -> list[BoardFinding]:
    """Edges carrying an outcome the card before them never names."""
    found: list[BoardFinding] = []
    for edge in board.edges:
        if not board.has(edge.from_key):
            continue
        declared = {o.name for o in board.p(edge.from_key).declared_outcomes()}
        found += [
            BoardFinding(
                anchor=f"edge:{edge.key}",
                field="on_outcomes",
                reason=f"This carries {outcome!r}, which the card before it never declares.",
                kind="structure",
            )
            for outcome in edge.on_outcomes
            if outcome not in declared
        ]
    return found


def outcomes_are_wired(board: Board) -> list[BoardFinding]:
    """Outcomes a card declares that lead nowhere.

    The most common real gap on a board, and the one worth making visible on the
    card rather than only in a list — three outcomes and two lines is something
    you can see.
    """
    return [
        BoardFinding(
            anchor=f"primitive:{card.key}",
            field="outcomes",
            reason=f"Nothing says what happens on {wiring.name!r}.",
            kind="structure",
        )
        for card in board.nodes()
        for wiring in board.outcomes(card.key)
        if not wiring.wired
    ]


# --- entities and references -----------------------------------------------


def entities_are_read(board: Board) -> list[BoardFinding]:
    """Entities nothing on the board looks at."""
    return [
        BoardFinding(
            anchor=f"primitive:{entity.key}",
            field="name",
            reason="Nothing on the board reads this.",
            severity="important",
            kind="structure",
        )
        for entity in board.entities()
        if not board.readers_of(entity.key)
    ]


def inputs_exist(board: Board) -> list[BoardFinding]:
    """Cards reading something that is not on the board."""
    return [
        BoardFinding(
            anchor=f"primitive:{card.key}",
            field="inputs",
            reason=f"This reads {key!r}, which is not on the board.",
            kind="structure",
        )
        for card in board.nodes()
        for key in card.declared_inputs()
        if not board.has(key)
    ]


def field_references_resolve(board: Board) -> list[BoardFinding]:
    """Cards reading a field the entity they name does not carry."""
    return [
        BoardFinding(
            anchor=f"primitive:{card.key}",
            field=field_name,
            reason=f"This reads {ref}, which that card does not have.",
        )
        for card in board.nodes()
        for field_name, refs in board.field_references(card).items()
        for ref in refs
        if not board.entity_has_field(ref.entity, ref.path)
    ]


def cardinality_resolves(board: Board) -> list[BoardFinding]:
    """Entities counted against a field that does not exist.

    "One COA per batch on the invoice" is what makes an expected count derivable
    rather than configured, so a `per` that does not resolve silently removes the
    only thing that knows how many to expect.
    """
    found: list[BoardFinding] = []
    for entity in board.entities():
        per = entity.config.cardinality.per
        if per is not None and not board.entity_has_field(per.entity, per.path):
            found.append(
                BoardFinding(
                    anchor=f"primitive:{entity.key}",
                    field="cardinality.per",
                    reason=f"These are counted against {per}, which that card does not have.",
                )
            )
    return found


def entities_are_recognisable(board: Board) -> list[BoardFinding]:
    """Entities that arrive with no way to tell one when you see it.

    The card reports this as merely important, because it cannot know how the
    entity gets here. An entity that **arrives** — captured by an event, sitting
    among several attachments — must be recognisable or nothing can pick it out.
    One a lookup returns needs no rule at all: the answer is whatever came back.
    Only the board knows which, so this is where the severity is decided.
    """
    return [
        BoardFinding(
            anchor=f"primitive:{entity.key}",
            field="identified_by",
            reason="Nothing says how to recognise this among the things that arrive.",
        )
        for entity in board.entities()
        if not entity.config.identified_by and _arrives(board, entity.key)
    ]


def fills_resolve(board: Board) -> list[BoardFinding]:
    """Checks writing a number into a field that does not exist.

    A fill target is the one kind of field reference no other rule sees, because
    every other reference *reads* and this one *writes*. Without this a check
    filling `shipment_summary.coa_totl` lints clean and reaches codegen, where
    the column silently never appears.
    """
    found: list[BoardFinding] = []
    for check in board.checks():
        for fill in check.config.fills:
            anchor = f"primitive:{check.key}"
            if _arrives(board, fill.field.entity):
                found.append(
                    BoardFinding(
                        anchor=anchor,
                        field="fills",
                        reason=f"This writes into {fill.field.entity!r}, which arrives from "
                        "outside. Only something the process produces can be written to.",
                    )
                )
            elif not board.entity_has_field(fill.field.entity, fill.field.path):
                found.append(
                    BoardFinding(
                        anchor=anchor,
                        field="fills",
                        reason=f"This fills in {fill.field}, which that card does not have.",
                    )
                )
    return found


def produced_fields_are_filled(board: Board) -> list[BoardFinding]:
    """Fields on a produced entity that nothing fills in.

    Once a board declares what it produces, every column with no check behind it
    becomes visible — which is how "every historical row reports an ASN count
    and your board produces none" stops being an observation somebody has to
    make and becomes a finding on the card.
    """
    filled: dict[str, set[str]] = {}
    for check in board.checks():
        for fill in check.config.fills:
            filled.setdefault(fill.field.entity, set()).add(fill.field.path)

    return [
        BoardFinding(
            anchor=f"primitive:{entity_key}",
            field="fields",
            reason=f"Nothing fills in {field_name!r}.",
            severity="important",
            kind="structure",
        )
        for entity_key, paths in filled.items()
        if board.has(entity_key)
        for field_name in board.p(entity_key).config.fields  # type: ignore[union-attr]
        if field_name not in paths
    ]


# --- the shape of the flow -------------------------------------------------


def events_lead_somewhere(board: Board) -> list[BoardFinding]:
    """Events after which nothing happens.

    An event is something arriving. After it arrives something has to follow, so
    an event is never where a process ends.
    """
    return [
        BoardFinding(
            anchor=f"primitive:{event.key}",
            field="outgoing",
            reason="Nothing happens after this arrives.",
            kind="structure",
        )
        for event in board.events()
        if not board.outgoing(event.key)
    ]


def dead_ends_are_endings(board: Board) -> list[BoardFinding]:
    """Cards where the process stops without saying it has ended."""
    return [
        BoardFinding(
            anchor=f"primitive:{card.key}",
            field="outgoing",
            reason="The process stops here, but this is not marked as an ending.",
            severity="important",
            kind="structure",
        )
        for card in board.terminals()
        if not _is_an_ending(card)
    ]


def steps_are_reachable(board: Board) -> list[BoardFinding]:
    """Cards nothing leads to."""
    return [
        BoardFinding(
            anchor=f"primitive:{card.key}",
            field="incoming",
            reason="Nothing leads here.",
            severity="important",
            kind="structure",
        )
        for card in board.unreachable()
    ]


RULES: tuple[Rule, ...] = (
    card_completeness,
    edge_endpoints_exist,
    edge_endpoints_are_steps,
    edge_outcomes_are_declared,
    outcomes_are_wired,
    entities_are_read,
    inputs_exist,
    field_references_resolve,
    cardinality_resolves,
    entities_are_recognisable,
    fills_resolve,
    produced_fields_are_filled,
    events_lead_somewhere,
    dead_ends_are_endings,
    steps_are_reachable,
)


def findings(board: Board) -> list[BoardFinding]:
    """Every finding, with one problem reported once at its worst severity.

    Rules overlap on purpose — a card says its recognition rule is merely
    important, and the board says it is blocking because nothing produces that
    entity. Merging on (anchor, field) means the process owner sees one blank
    rather than two, at the severity that actually gates the freeze.
    """
    worst: dict[tuple[str, str], BoardFinding] = {}
    for rule in RULES:
        for finding in rule(board):
            key = (finding.anchor, finding.field)
            current = worst.get(key)
            if current is None or _outranks(finding.severity, current.severity):
                worst[key] = finding
    return sorted(worst.values(), key=lambda f: (-SEVERITY_RANK[f.severity], f.anchor, f.field))


def blocking(board: Board) -> list[BoardFinding]:
    """Only the findings that stop a board being frozen."""
    return [f for f in findings(board) if f.severity == "blocking"]


def _outranks(candidate: Severity, current: Severity) -> bool:
    return SEVERITY_RANK[candidate] > SEVERITY_RANK[current]


def _is_an_ending(card: FlowNode) -> bool:
    return isinstance(card, ActionPrimitive) and card.config.is_terminal


def _arrives(board: Board, entity_key: str) -> bool:
    """Whether this entity comes in with an event rather than being fetched."""
    return any(entity_key in event.config.captures for event in board.events())
