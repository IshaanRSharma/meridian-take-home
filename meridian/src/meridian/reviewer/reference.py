"""Turning a written procedure into claims about elements of the board.

The whiteboard exists because most processes have no document. When one does,
it is the highest-precision question source available, and for a reason worth
being precise about: every other question the reviewer asks is *speculative* —
it notices silence and guesses that the silence matters. A document makes the
question **checkable**. "Your procedure says the report carries a description of
the problem and this step sends two identifiers" is not a hunch about tacit
knowledge; it is a difference between two things that both exist.

So the document is not summarised and it is not pasted in whole. It is aligned:
each thing it says is attached to the one element of the board it bears on, using
the same anchor vocabulary and the same validation as a settled statement. That
alignment is the entire mechanism. Put beside what the card actually says, a
claim does one of three things, and two of them are questions:

    it agrees                 nothing to ask
    it disagrees              the strongest question in the system
    the drawing is silent     a rule nobody drew

What comes back is a :class:`DocumentClaim`, which is deliberately not an
Assertion. A document says what somebody wrote down once; that is evidence about
the process, not a decision about it, and nobody has confirmed it is still true.
Only a settled conversation crosses the freeze — so a claim can start a
conversation and can never be the answer to one.

**Under-extraction is the failure to design against.** A model asked "what does
this document say" volunteers a fraction of it. So the instruction is to work
through the document rather than recall it, and to attach every rule it finds,
including the ones the board already covers — the agreements cost a line each
and their absence would be indistinguishable from the model not having looked.
"""

from collections.abc import Sequence

from pydantic import BaseModel, Field, ValidationError

from meridian.core.llm import Task, Transport, structured
from meridian.domain.graph import Board
from meridian.domain.review import Anchor, DocumentClaim, ReferenceDoc

SYSTEM = """\
You are given a written procedure for a business process, and a drawing of that
same process made by the person who runs it. The two were made separately and
neither is authoritative.

Your job is to say what the DOCUMENT states, attached to the part of the drawing
each statement is about. You are not judging the drawing and you are not writing
questions — somebody else does both, and they can only do it with this list.

Work THROUGH the document. Take it a paragraph at a time and ask what each one
would tell somebody who had to build this. Do not summarise it and do not skim
for highlights: a rule you passed over is a rule nobody will ever ask about, and
the most valuable statements are usually the small conditional ones — who gets
told, by when, what happens when something is late, what counts as acceptable.

Attach every statement, including ones the drawing already appears to handle. An
agreement costs a line; a silence is indistinguishable from you not having read
that paragraph.

Each statement carries:

  anchor      one of the strings in `elements`, copied verbatim. Nothing else
              resolves, and a statement anchored elsewhere is discarded.
  statement   one sentence, in the document's own terms. Say what the procedure
              requires, not what it "mentions" — "the report goes to the
              supervisor within one working day", never "the document discusses
              reporting timelines".

Two things to leave out. Anything the document does not actually say — no
inference, no filling in what it obviously implies, because a claim you invented
would be put to somebody as though their own procedure said it. And anything
that bears on no element of the drawing: if there is nothing to attach it to,
it is not yet part of this process.\
"""


class Stated(BaseModel):
    """One thing the document says, about one element."""

    anchor: str
    statement: str


class Statements(BaseModel):
    """Everything the document says that bears on this board."""

    statements: list[Stated] = Field(default_factory=list)


async def read(
    board: Board,
    documents: Sequence[ReferenceDoc],
    *,
    transport: Transport | None = None,
) -> tuple[DocumentClaim, ...]:
    """What every attached document says, aligned onto elements of the board.

    One call per document rather than one for all of them: the statements have to
    carry which document they came from, and a single call over three documents
    reliably loses track of that. Costs are per-round and small.

    Args:
        board: what the anchors are checked against.
        documents: attached reference documents. Ones with no text are skipped.
        transport: the function that talks to OpenAI. Tests pass a fake.

    Returns:
        One claim per statement whose anchor resolves, in the order proposed.
    """
    found: list[DocumentClaim] = []
    for document in documents:
        if not document.text:
            continue
        completed = await structured(
            Task.REVIEW,
            Statements,
            SYSTEM,
            _payload(board, document),
            transport=transport,
        )
        found += [
            DocumentClaim(anchor=anchor, statement=said.statement, source=document.filename)
            for said in completed.value.statements
            if (anchor := _resolve(board, said.anchor)) is not None
        ]
    return tuple(found)


def elements(board: Board) -> list[str]:
    """Every reference that resolves on this board, in the form an anchor takes.

    The same list ``distill`` hands over, and for the same reason: the model
    reliably names the right card and reliably drops the prefix, so handing over
    the vocabulary beats describing it.
    """
    refs = ["board"]
    refs += sorted({f"group:{p.group_key}" for p in board.primitives if p.group_key})
    refs += [f"primitive:{p.key}" for p in board.primitives]
    refs += [f"edge:{e.key}" for e in board.edges]
    refs += [
        f"entity_field:{entity.key}.{field}"
        for entity in board.entities()
        for field in entity.config.fields
    ]
    return refs


def _payload(board: Board, document: ReferenceDoc) -> str:
    """The document, and the elements a statement may be attached to."""
    named = [
        f"{ref}   {_describe(board, ref)}" if _describe(board, ref) else ref
        for ref in elements(board)
    ]
    return (
        f"THE DOCUMENT ({document.kind}, {document.filename})\n{document.text}\n\n"
        "ELEMENTS OF THE DRAWING (attach each statement to exactly one, "
        "copied verbatim)\n" + "\n".join(named)
    )


def _describe(board: Board, ref: str) -> str:
    """What the owner called this element, so the model can match prose to it.

    A bare list of keys makes the model guess which slug a paragraph is about.
    The names are the owner's own words and the document is in the same
    vocabulary, so this is what makes the alignment work at all.
    """
    kind, _, key = ref.partition(":")
    if kind != "primitive" or not board.has(key):
        return ""
    card = board.p(key)
    return f"({card.primitive_type}) {card.config.name or ''}".strip()


def _resolve(board: Board, ref: str) -> Anchor | None:
    """The element this reference names, or ``None`` if the board has no such thing."""
    try:
        anchor = Anchor.parse(ref)
    except (ValidationError, ValueError):
        return None

    key = anchor.key or ""
    match anchor.kind:
        case "board":
            return anchor
        case "group":
            found = any(p.group_key == key for p in board.primitives)
        case "primitive":
            found = board.has(key)
        case "edge":
            found = any(e.key == key for e in board.edges)
        case "entity_field":
            entity, _, path = key.partition(".")
            found = board.entity_has_field(entity, path)
    return anchor if found else None


__all__ = ["SYSTEM", "Stated", "Statements", "elements", "read"]
