"""What survives a settled conversation.

A comment is a question with a history. An assertion is what is left once it is
answered — and only assertions cross the freeze, because a transcript is how a
decision was reached and generated code should read the decision.

The whole design is in the difference between how the two are anchored. A
conversation ranges over several elements at once, so a comment has many
anchors. A settled statement compiles into exactly one file, so an assertion has
one. Distilling a comment about three elements therefore yields up to three
statements, each carrying the part of the answer that belongs to its element and
nothing else.

Every anchor the model proposes is checked against the board and dropped if it
resolves to nothing. A statement pointing at a card that does not exist would
vanish silently at the freeze; dropping it here is the same loss, made visible.

A dismissed concern still distils. "Expiry is not checked at this stage" is
knowledge — it stops a later round re-asking, and it tells a code generator the
case was considered rather than missed.
"""

import json
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from meridian.compiler import context, serialize
from meridian.core.llm import Task, Transport, structured
from meridian.domain.graph import Board
from meridian.domain.review import Anchor, Assertion, Thread

AssertionKind = Literal[
    "rule",
    "exception",
    "timing",
    "owner",
    "terminology",
    "constraint",
    "negative",
]

SYSTEM = """\
You are turning one settled conversation about a business process into the
statements that survive it.

The conversation ranges over several elements at once. A statement is about
exactly one element and compiles into exactly one file, so split the answer:
give each element the part that belongs to it, and nothing else. An answer that
settled one thing yields one statement.

Write what was decided, never what was asked. "A frozen membership reopens by
itself after thirty days" — not "the owner was asked how long the freeze lasts".
The reader is a code generator that never sees this conversation.

If the concern was dismissed, the dismissal is what survives: state what is *not*
done and why the case does not arise.

Each statement carries:

  anchor      one of the strings in `elements`, copied verbatim. Nothing else
              resolves, and a statement anchored elsewhere is discarded. Prefer
              the elements under `thread.about`; reach outside them only when the
              answer genuinely settles something about another element that
              already exists.
  kind        rule · exception · timing · owner · terminology · constraint · negative
  statement   one sentence of prose, in the process owner's own vocabulary.
  constraint  only when the statement can be checked by machine against a real
              extracted value — a field format, a bound, a permitted set. A JSON
              object literal, e.g. {"pattern": "^\\\\d{7}$"}. Required whenever
              kind is `constraint`, and omitted otherwise.

Say nothing the conversation did not settle.

`already_known` is what has been settled about these same elements. Add what is
NEW. If this conversation only confirmed something already there, say nothing
about it — but never leave out something new because it sounds similar to
something already known, because similar is not the same and the difference is
usually the whole point.\
"""


class Statement(BaseModel):
    """One settled thing about one element, as the model proposes it.

    ``anchor`` is the ``<kind>:<key>`` string rather than a nested object so the
    model copies back a reference it was handed, and so a reference to something
    that does not exist can be dropped instead of failing the whole response.
    """

    anchor: str
    kind: AssertionKind
    statement: str
    constraint: str | None = None


class Distillation(BaseModel):
    """Everything one conversation settled."""

    statements: list[Statement] = Field(default_factory=list)


async def distil(
    board: Board,
    thread: Thread,
    recorded: Sequence[Assertion] = (),
    *,
    transport: Transport | None = None,
) -> tuple[Assertion, ...]:
    """The statements this conversation settled, one element each.

    Args:
        board: what the anchors are checked against.
        thread: the conversation, including every turn.
        recorded: what is already settled. Filtered to the elements this
            conversation is about before it is shown, so a statement from an
            unrelated part of the board can never suppress one here.
        transport: the function that talks to OpenAI. Tests pass a fake.

    Returns:
        One assertion per statement whose anchor resolves, in the order proposed.
    """
    completed = await structured(
        Task.DISTIL,
        Distillation,
        SYSTEM,
        _payload(board, thread, recorded),
        transport=transport,
    )

    settled: list[Assertion] = []
    for proposed in completed.value.statements:
        anchor = _resolve(board, proposed.anchor)
        if anchor is None:
            continue
        checkable = _as_object(proposed.constraint)
        settled.append(
            Assertion(
                thread_id=thread.id,
                anchor=anchor,
                kind=_kind(proposed.kind, thread.status, checkable),
                statement=proposed.statement,
                constraint_json=checkable,
                round=thread.round,
            )
        )
    return tuple(settled)


def _kind(
    proposed: AssertionKind, status: str, checkable: dict[str, object] | None
) -> AssertionKind:
    """What this statement actually is, which is not always what it was called.

    ``constraint`` is a promise that something can validate it against a real
    extracted value. A statement carrying nothing checkable has not made that
    promise whatever it called itself, so it is recorded as the rule it is —
    the prose is still true and still what a code generator reads, and losing a
    settled answer because the model reached for the wrong label would be the
    worse trade. Observed on the first live run: the model labelled a matching
    rule a constraint and passed no object, and the whole round raised.
    """
    if status == "rejected":
        return "negative"
    if proposed == "constraint" and checkable is None:
        return "rule"
    return proposed


JUDGE = """\
You are handed a drawing of a business process and a list of statements somebody
made about it afterwards.

For each statement, answer one question: **could you have written this from the
drawing alone?**

Say yes when the statement only restates what the drawing already shows — that a
step follows another, that a check has certain outcomes, that a failure leads to
a reporting step, that a card reads a particular thing. All of that is already in
the drawing, in a form a machine reads directly.

Say no when it carries something the drawing cannot express: what a value means,
what counts as two things being the same, how long to wait, who decides, what
happens in a situation the drawing does not name, or why something is *not* done.

Judge the statement as written, not the topic. "A payment is checked against the
membership" restates the drawing. "A payment matches by member number, not by the
name on the card" does not, even though it is about the same check.

Be strict. A statement that merely sounds specific, but adds nothing a code
generator could not read off the drawing, is a restatement.\
"""


class Judgement(BaseModel):
    """Whether one statement said anything the board had not."""

    index: int
    """Which statement, by position in the list given. Never reorder."""

    restates_the_drawing: bool
    why: str


class Judged(BaseModel):
    """One verdict per statement, in the order they were given."""

    judgements: list[Judgement] = Field(default_factory=list)


async def paraphrases(
    board: Board, settled: Sequence[Assertion], *, transport: Transport | None = None
) -> tuple[Assertion, ...]:
    """The settled statements that only say again what the board already says.

    The measurement that separates a spec which *encodes* the answers from one
    that merely *contains* them. A statement like "the process reports a COA
    discrepancy when the COA check fails" is readable straight off the edges — it
    is correctly anchored, correctly inlined, correctly checksummed, and worth
    nothing, because a code generator could have derived it without being told.

    Reported rather than dropped, deliberately. A restatement is still true, and
    losing a settled answer to a model's judgement call is the worse failure —
    the same trade as demoting a mislabelled constraint instead of refusing it.
    What this gives is a number: the fraction of the spec that came from a person
    rather than from the drawing, which is the reviewer's actual yield.

    One call for the whole list rather than one per statement. The judgements can
    anchor on each other that way, which is a real cost, and it is the right one
    at twelve statements a round.
    """
    if not settled:
        return ()

    payload = json.dumps(
        {
            "drawing": serialize.review_payload(board),
            "statements": [a.statement for a in settled],
        },
        indent=2,
    )
    completed = await structured(Task.DISTIL, Judged, JUDGE, payload, transport=transport)

    restated = {
        judged.index
        for judged in completed.value.judgements
        if judged.restates_the_drawing and 0 <= judged.index < len(settled)
    }
    return tuple(a for index, a in enumerate(settled) if index in restated)


def _resolve(board: Board, ref: str) -> Anchor | None:
    """The element this reference names, or ``None`` if the board has no such thing."""
    try:
        anchor = Anchor.parse(ref)
    except ValidationError:
        return None

    key = anchor.key or ""
    match anchor.kind:
        case "board":
            return anchor
        case "group":
            # A region exists exactly while something is in it, which is the
            # only sense in which a group is on the board at all.
            found = any(p.group_key == key for p in board.primitives)
        case "primitive":
            found = board.has(key)
        case "edge":
            found = any(e.key == key for e in board.edges)
        case "entity_field":
            entity, _, path = key.partition(".")
            found = board.entity_has_field(entity, path)
    return anchor if found else None


def _as_object(literal: str | None) -> dict[str, object] | None:
    """The machine-checkable form, or ``None`` when there is not one.

    Prose and a JSON array are the same thing here: something no validator can
    apply to an extracted value. Returning ``None`` hands the judgement to
    ``Assertion``, which already knows a constraint needs one.
    """
    if literal is None:
        return None
    try:
        parsed = json.loads(literal)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _payload(board: Board, thread: Thread, recorded: Sequence[Assertion] = ()) -> str:
    """The conversation, what its elements already know, and what may be anchored to.

    ``already_known`` is scoped by ``reaches`` rather than being the whole set,
    and that is the whole safety property. Two subgraphs can hold near-identical
    rules — a matching rule on one check and another on a different one — and
    both are needed, because a statement is inlined into every card its anchor
    reaches and each generated file needs its own. Shown everything, a model
    asked not to repeat itself would suppress the second. Shown only what these
    elements already know, there is nothing to wrongly suppress.

    Framed as *add what is new* rather than *refuse what is similar*, too: a
    wrong call then writes a restatement, which `paraphrases` already reports and
    nobody loses. The other framing deletes a settled answer.
    """
    about = [
        board.p(anchor.key)
        for anchor in thread.anchors
        if anchor.kind == "primitive" and anchor.key and board.has(anchor.key)
    ]
    known = sorted(
        {
            f"[{a.kind}] {a.statement}"
            for a in recorded
            if a.is_active()
            and (
                a.anchor in thread.anchors or any(context.reaches(card, a.anchor) for card in about)
            )
        }
    )
    return json.dumps(
        {
            "already_known": known,
            "thread": {
                "category": thread.category,
                "status": thread.status,
                "question": thread.question,
                "reason": thread.reason,
                "about": [str(anchor) for anchor in thread.anchors],
                "conversation": [
                    {"author": message.author, "said": message.body}
                    for message in sorted(thread.messages, key=lambda m: m.seq)
                ],
            },
            "elements": _elements(board),
        },
        indent=2,
    )


def _elements(board: Board) -> list[str]:
    """Every reference that resolves on this board, in the form an anchor takes.

    Anchors are validated after the fact regardless, so this is not the check —
    it is what stops the model guessing keys and spending the round on
    statements that get dropped.
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


__all__ = ["Distillation", "Judged", "Judgement", "Statement", "distil", "paraphrases"]
