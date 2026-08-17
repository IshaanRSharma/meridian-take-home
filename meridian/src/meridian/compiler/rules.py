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
            severity="blocking",
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
            severity="blocking",
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
                severity="blocking",
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
            severity="blocking",
        )
        for card in board.nodes()
        for wiring in board.outcomes(card.key)
        if not wiring.wired
    ]


# --- entities and references -----------------------------------------------


def entities_are_read(board: Board) -> list[BoardFinding]:
    """Entities nothing on the board looks at.

    An entity a Check *fills* is exempt, because the row a process produces is
    consumed outside the process — by whoever reads the report. Every other
    entity exists to be read by something on the board, so one nothing reads is
    a card somebody forgot to wire up. Same asymmetry `_producers_of` documents:
    a Check declares it produces nothing, which is true of what it reads and
    false of what it writes.
    """
    filled = {fill.field.entity for check in board.checks() for fill in check.config.fills}
    return [
        BoardFinding(
            anchor=f"primitive:{entity.key}",
            field="name",
            reason="Nothing on the board reads this.",
            severity="important",
            kind="structure",
        )
        for entity in board.entities()
        if not board.readers_of(entity.key) and entity.key not in filled
    ]


def inputs_exist(board: Board) -> list[BoardFinding]:
    """Cards reading something that is not on the board."""
    return [
        BoardFinding(
            anchor=f"primitive:{card.key}",
            field="inputs",
            reason=f"This reads {key!r}, which is not on the board.",
            kind="structure",
            severity="blocking",
        )
        for card in board.nodes()
        for key in card.declared_inputs()
        if not board.has(key)
    ]


def produced_entities_exist(board: Board) -> list[BoardFinding]:
    """A step claiming to produce something the board does not have.

    The mirror of `inputs_exist`, and it matters most for a lookup. A lookup is
    the third direction data moves — in from a system, mid-process — and the
    whole reason it is one card rather than three is that its answer becomes a
    thing other steps can read. `produces` naming an entity nobody drew makes
    that answer unaddressable: no Check can list it in `inputs`, no `FieldRef`
    can reach into it, and a generator writes the result of a live API call into
    nowhere.

    Blocking, because a reference that does not resolve is not a missing value —
    it is a drawing that does not work as a process.
    """
    return [
        BoardFinding(
            anchor=f"primitive:{card.key}",
            field="produces",
            reason=f"This produces {key!r}, which is not on the board.",
            kind="structure",
            severity="blocking",
        )
        for card in board.nodes()
        for key in card.produces()
        if not board.has(key)
    ]


def something_produces_what_a_step_reads(board: Board) -> list[BoardFinding]:
    """Entities a step reads that nothing on the board ever brings into existence.

    The mirror of `produced_entities_exist`, and the one direction nothing
    covered. Every other rule passes it: the card is on the board, so
    `inputs_exist` is happy; a step reads it, so `entities_are_read` is happy;
    it is in `inputs`, so `references_are_declared` is happy. And
    `inputs_arrive_before_they_are_read` asks whether the producer runs *early
    enough*, which is vacuously true when there is no producer at all — so a
    board reading an entity nothing ever creates lints exactly as clean as one
    that does not.

    What reaches codegen is a Check whose `inputs` name something that will never
    exist at runtime. Blocking, because an unresolvable reference is not a missing
    value: it is a drawing that does not work as a process.
    """
    return [
        BoardFinding(
            anchor=f"primitive:{card.key}",
            field="inputs",
            reason=f"This reads {_name_of(board, key)}, and nothing on the board produces it.",
            kind="structure",
            severity="blocking",
        )
        for card in board.nodes()
        for key in sorted(set(card.reads()))
        if board.has(key) and not _producers_of(board, key)
    ]


def one_way_out_of_every_step(board: Board) -> list[BoardFinding]:
    """Steps with several ways out and nothing to choose between them.

    This vocabulary is sequential: an edge carries an outcome or it is the one
    way on. Two unconditional edges leaving one step is how a process owner draws
    *"these both happen, in no particular order"* — and nothing here can express
    that, so the honest thing is to say so on the card rather than to accept the
    drawing and then quietly walk half of it.

    Quietly is the operative word. Before this rule, such a board linted clean,
    reported every step as reachable, and dry-ran down whichever branch was drawn
    first — reporting the other as never reached and the situation as having
    reached an ending. A scenario named for a check on the dropped branch passed
    without that check ever running, which is the worst failure available to a
    gap detector: a clean bill of health on the thing it was built to interrogate.
    """
    return [
        BoardFinding(
            anchor=f"primitive:{card.key}",
            field="outgoing",
            reason="Several things leave this step and nothing says which happens "
            "first. Order them, or make each one depend on how this came out.",
            kind="structure",
            severity="blocking",
        )
        for card in board.nodes()
        if len([e for e in board.outgoing(card.key) if not e.on_outcomes]) > 1
    ]


def field_references_resolve(board: Board) -> list[BoardFinding]:
    """Cards reading a field the entity they name does not carry."""
    return [
        BoardFinding(
            anchor=f"primitive:{card.key}",
            field=field_name,
            reason=f"This reads {ref}, which that card does not have.",
            severity="blocking",
        )
        for card in board.nodes()
        for field_name, refs in board.field_references(card).items()
        for ref in refs
        if not board.entity_has_field(ref.entity, ref.path)
    ]


def references_are_declared(board: Board) -> list[BoardFinding]:
    """Cards reading a field off something they were never given.

    Every other reference rule asks whether a field exists. This asks whether
    the step receives the card it reads from, which is a different failure and
    invisible to the others: the field is real, the entity is on the board, and
    the step still never gets it. It reaches codegen as an email quoting an
    invoice number nobody passed in, and it silently cuts that card out of the
    scope chain, so a statement about the entity never arrives either.
    """
    found: list[BoardFinding] = []
    for card in board.nodes():
        held = {*card.reads(), *card.produces()}
        missing = sorted(
            {ref.entity for refs in board.field_references(card).values() for ref in refs} - held
        )
        if not missing:
            continue
        named = " and ".join(_name_of(board, key) for key in missing)
        found.append(
            BoardFinding(
                anchor=f"primitive:{card.key}",
                field="inputs",
                reason=f"This reads from {named}, which this step is not given.",
                severity="blocking",
            )
        )
    return found


def inputs_arrive_before_they_are_read(board: Board) -> list[BoardFinding]:
    """Cards reading something nothing has produced by the time they run.

    Every other reference rule asks whether a thing *exists* on the board. This
    asks whether it exists **yet**, which is a different question and the only
    one that depends on the flow rather than on the cards.

    It stayed invisible while every entity arrived with the Event, because an
    Event is upstream of everything. A lookup is the first entity with a
    position in the process, and a Check filling an output row is the second —
    and both were reordered on purpose to produce a board that lints completely
    clean while reading nothing forever. `fills_resolve` checks writes,
    `field_references_resolve` checks reads, and until now nothing joined them.

    A cycle counts as upstream, correctly: a `repeat` edge means the producer
    genuinely can have run first.
    """
    found: list[BoardFinding] = []
    for card in board.nodes():
        # A card counts as its own producer. An Event that captures an entity
        # both brings it in and holds it, and a lookup Action reads the inputs
        # it searches by — neither is waiting on anything earlier.
        before = board.upstream(card.key) | {card.key}
        late = sorted(
            entity_key
            for entity_key in card.reads()
            if (producers := _producers_of(board, entity_key)) and not producers & before
        )
        if not late:
            continue
        named = " and ".join(_name_of(board, key) for key in late)
        found.append(
            BoardFinding(
                anchor=f"primitive:{card.key}",
                field="inputs",
                reason=f"This reads {named}, but nothing produces it before this step runs.",
                severity="blocking",
            )
        )
    return found


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
                    severity="blocking",
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
            severity="important",
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
                        severity="blocking",
                    )
                )
            elif not board.entity_has_field(fill.field.entity, fill.field.path):
                found.append(
                    BoardFinding(
                        anchor=anchor,
                        field="fills",
                        reason=f"This fills in {fill.field}, which that card does not have.",
                        severity="blocking",
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
            field=f"fields.{field_name}",
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
            severity="blocking",
        )
        for event in board.events()
        if not board.outgoing(event.key)
    ]


def something_starts_the_process(board: Board) -> list[BoardFinding]:
    """A board with steps but no way into them.

    Reachability is measured from the events, so a board with none reports every
    step as unreachable — one problem wearing N findings, not one of which names
    the cause. This says the thing that is actually wrong, once.
    """
    if board.events() or not board.nodes():
        return []
    return [
        BoardFinding(
            anchor="board",
            field="events",
            reason="Nothing starts this process.",
            severity="blocking",
            kind="structure",
        )
    ]


def dead_ends_are_endings(board: Board) -> list[BoardFinding]:
    """Actions where the process stops without saying it has ended.

    Only an Action carries ``is_terminal``, so only an Action can fail to set it.
    Reporting this on a Check would name a field that does not exist — an
    unclearable finding, and a blocking one, which is the single combination
    that leaves someone stuck on the canvas with no move available. A Check that
    stops has outcomes leading nowhere, and `outcomes_are_wired` already says so
    in a sentence with something to do about it.
    """
    return [
        BoardFinding(
            anchor=f"primitive:{card.key}",
            field="outgoing",
            reason="The process stops here, but this is not marked as an ending.",
            severity="blocking",
            kind="structure",
        )
        for card in board.terminals()
        if isinstance(card, ActionPrimitive) and not card.config.is_terminal
    ]


def steps_are_reachable(board: Board) -> list[BoardFinding]:
    """Cards nothing leads to.

    Silent when the board has no events at all: reachability is measured from
    them, so every step would be reported and none of the reports would name the
    cause. `something_starts_the_process` says that once instead.
    """
    if not board.events():
        return []
    return [
        BoardFinding(
            anchor=f"primitive:{card.key}",
            field="incoming",
            reason="Nothing leads here.",
            severity="blocking",
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
    something_starts_the_process,
    inputs_exist,
    produced_entities_exist,
    something_produces_what_a_step_reads,
    one_way_out_of_every_step,
    references_are_declared,
    inputs_arrive_before_they_are_read,
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


def _name_of(board: Board, key: str) -> str:
    """What the process owner called a card, for a message they have to read."""
    return (board.p(key).config.name or key) if board.has(key) else key


def _producers_of(board: Board, entity_key: str) -> set[str]:
    """Every step that brings this entity into existence, filling included.

    ``Board.producers_of`` cannot see a Check that ``fills`` an output entity,
    because a Check declares it produces nothing — which is true of the entities
    it reads and false of the row it writes. Flow order needs both.
    """
    produced = {node.key for node in board.producers_of(entity_key)}
    filled = {
        check.key
        for check in board.checks()
        for fill in check.config.fills
        if fill.field.entity == entity_key
    }
    return produced | filled


def _arrives(board: Board, entity_key: str) -> bool:
    """Whether this entity comes in with an event rather than being fetched."""
    return any(entity_key in event.config.captures for event in board.events())
