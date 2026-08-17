"""Conversations about a board, and the settled statements they distil into.

A Thread is a question with a history. An Assertion is what survives once the
question is answered. Only assertions cross the freeze — a transcript is how a
decision was reached, and generated code should read the decision.

The difference in how they are anchored is the whole design. A conversation
ranges over several elements at once, so a Thread has many anchors. A settled
statement compiles into exactly one place, so an Assertion has one. Distilling a
thread with three anchors therefore yields up to three assertions.

Status is not a label the answerer sets. ``answered`` means the knowledge now
exists; ``resolved`` means the board reflects it, and only re-running the
scenario that raised the question can establish that.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from meridian.domain.primitives import BoardKey, DomainModel, Severity

ThreadStatus = Literal["open", "answered", "rejected", "resolved"]

# Two loops, not one set of labels.
#
#   COMMENT LOOP — settle the business knowledge
#     open ──the owner answers──▶ answered
#          ──not applicable─────▶ rejected
#
#   REVISION LOOP — make the drawing show it
#     answered ──the board now represents it──▶ resolved
#              ──the scenario still fails─────▶ open
#
# `answered` means the rule is known. `resolved` means the canvas reflects it,
# and those come apart constantly: knowing "the shipment comes back once the
# paperwork is fixed" and having drawn the repeat edge are different states.
# Allowing open → resolved would collapse the revision loop out of existence,
# which is how a review loop becomes decorative.
#
# A lint blank goes through both, quickly: filling the field IS the answer, and
# the canvas reflects it immediately.
#
# `rejected` is terminal but is not a delete: it compiles into the spec as
# negative knowledge, so a later round does not re-ask and codegen knows the
# case was considered and dismissed.
_ALLOWED_TRANSITIONS: dict[ThreadStatus, frozenset[ThreadStatus]] = {
    "open": frozenset({"answered", "rejected"}),
    "answered": frozenset({"resolved", "rejected", "open"}),
    "rejected": frozenset(),
    "resolved": frozenset({"open"}),
}


class Anchor(DomainModel):
    """The element a conversation or a statement is about.

    Always one element with a key that survives an edit. A path or a region is a
    *set* of anchors rather than an anchor kind, because a path has no identity
    once one of its edges is deleted. ``group`` is the exception: membership
    changes but the group itself persists.
    """

    kind: Literal["board", "group", "primitive", "edge", "entity_field"]
    key: str | None = None

    @model_validator(mode="after")
    def _board_is_the_only_keyless_anchor(self) -> Self:
        if (self.kind == "board") != (self.key is None):
            msg = "only a board anchor has no key, and a board anchor never has one"
            raise ValueError(msg)
        return self

    @classmethod
    def parse(cls, ref: str) -> Self:
        """Build from ``board`` or ``<kind>:<key>``."""
        kind, _, key = ref.partition(":")
        return cls(kind=kind, key=key or None)  # type: ignore[arg-type]

    def __str__(self) -> str:
        """Render in the form ``parse`` accepts."""
        return self.kind if self.key is None else f"{self.kind}:{self.key}"


class Evidence(DomainModel):
    """What a structural claim cites.

    A reviewer with graph tools can explore freely, but it may not assert
    something the board does not do. Every claim about structure names the call
    that produced it, and validation drops a thread whose evidence does not
    reproduce.
    """

    tool: str
    result: str
    detail: dict[str, object] | None = None


class CommentMessage(DomainModel):
    """One turn in a conversation."""

    seq: int
    author: Literal["ai", "human"]
    body: str
    created_at: datetime | None = None


class Thread(DomainModel):
    """A question about the board, and everything said about it."""

    id: UUID | None = None
    category: Literal[
        "missing_path",
        "ambiguous_rule",
        "missing_context",
        "undefined_exception",
        "undefined_timing",
        "redundancy",
        "spec_gap",
    ]
    severity: Severity = "important"
    status: ThreadStatus = "open"
    origin: Literal["lint", "scenario", "semantic", "reduction", "repair"] = "scenario"
    round: int = 1
    question: str
    # Why it is being asked, kept apart from the question itself. "What happens
    # after the COA is reported?" reads very differently with "the SOP ends at
    # reporting and never says what closes the shipment" beside it.
    reason: str | None = None
    anchors: tuple[Anchor, ...] = ()
    messages: tuple[CommentMessage, ...] = ()
    scenario_key: BoardKey | None = None
    evidence: Evidence | None = None
    # A structural question's identity, derived from the graph rather than from
    # its wording — `sequence:primitive:invoice_complete|edge:e2|…`. The same
    # board raises the same key every round, so dedup is an exact match and a
    # *rejected* question does not come back either. Null for questions a model
    # invented, which have no structural identity and are deduped by the model
    # having every prior thread in front of it.
    decision_key: str | None = None
    resolved_at: datetime | None = None

    def may_become(self, status: ThreadStatus) -> bool:
        """Whether this thread can legally move to that status."""
        return status in _ALLOWED_TRANSITIONS[self.status]

    def primary_anchor(self) -> Anchor | None:
        """The element this is mostly about, which is the first one named."""
        return self.anchors[0] if self.anchors else None

    def is_settled(self) -> bool:
        """Whether this no longer blocks a freeze."""
        return self.status in ("resolved", "rejected")


class Assertion(DomainModel):
    """One settled statement about one element.

    ``statement`` is prose because its consumer is a code generator, not a
    parser. ``constraint_json`` exists only where a statement is machine
    checkable — a field format that can be validated against real extracted
    values before anything is generated.
    """

    id: UUID | None = None
    thread_id: UUID | None = None
    anchor: Anchor
    kind: Literal[
        "rule",
        "exception",
        "timing",
        "owner",
        "terminology",
        "constraint",
        "negative",
    ]
    statement: str
    constraint_json: dict[str, object] | None = None
    round: int = 1
    superseded_by: UUID | None = None

    @model_validator(mode="after")
    def _a_constraint_must_be_checkable(self) -> Self:
        if self.kind == "constraint" and self.constraint_json is None:
            msg = "a constraint assertion needs constraint_json or nothing can check it"
            raise ValueError(msg)
        return self

    def is_active(self) -> bool:
        """Whether this statement is still the current one for its anchor."""
        return self.superseded_by is None


DocKind = Literal["sop", "policy", "email", "other"]
"""What a reference document is. Named because three surfaces spell it out.

The database has the same four in a CHECK constraint, and a route or a CLI option
that wrote them again is a fourth place to forget one. Typed here, the boundary
rejects a bad value with the field name before any SQL runs, and the generated
frontend types carry the enum rather than `string`.
"""


class ReferenceDoc(DomainModel):
    """A document that *describes* the process, rather than flowing through it.

    An SOP, a policy, a training note. The distinction from an Entity is which
    direction it points: an invoice is data the process reads, and an SOP is a
    claim about what the process should do. So this is never a card, never in the
    frozen spec, and read only while questions are being asked.
    """

    id: UUID | None = None
    kind: DocKind = "sop"
    filename: str
    text: str | None = None


class DocumentClaim(DomainModel):
    """Something a reference document says about one element of the board.

    Deliberately **not** an :class:`Assertion`. An assertion is settled — a
    person was asked and answered, and only settled statements cross the freeze.
    A document is evidence: it says what somebody wrote down once, which is not
    the same as what the process does now, and nobody has confirmed it. So this
    type exists to be un-freezable by construction.

    Anchored the same way an assertion is, and validated against the board the
    same way, because the value is in the alignment: put beside what the card
    actually says, a claim either agrees, disagrees, or covers something the
    drawing is silent about — and the second and third are questions.
    """

    anchor: Anchor
    statement: str
    source: str
    """Which document said it, so a question can cite where it came from."""


class Scenario(DomainModel):
    """A situation the board is asked to account for.

    ``outcomes`` is exactly what ``Board.dry_run`` takes, so a scenario is
    runnable rather than merely described — which is what lets a thread cite a
    trace instead of an opinion.

    A card is answered once and for all with a string, or visit by visit with a
    sequence. The sequence is how a resubmission is described: the COA check
    comes out ``missing_coa`` on Tuesday and ``pass`` on Thursday once the
    corrected document arrives. Stored as a tuple because these models are
    frozen, and a tuple is a sequence, so the promise above still holds.
    """

    key: BoardKey
    kind: Literal["happy", "variant", "probe"]
    description: str
    outcomes: dict[str, str | tuple[str, ...]] = Field(default_factory=dict)
    start: BoardKey | None = None
    expected_terminal: BoardKey | None = None
    round: int = 1
    # Where the last walk ended up. Kept on the scenario rather than recomputed,
    # because resolving a thread means re-running the walk that raised it and
    # comparing — and "it dead-ended before, it reaches a terminal now" is the
    # whole proof. `None` until it has been run.
    dryrun_result: Literal["reached_terminal", "dead_end", "undefined_branch", "loop"] | None = None
