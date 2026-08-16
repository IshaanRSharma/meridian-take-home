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

# open ──answer──▶ answered ──board reflects it──▶ resolved
#  └──reject──▶ rejected                │
#                                       └── the scenario still fails ──▶ open
#
# `rejected` is terminal but is not a delete: it compiles into the spec as
# negative knowledge, so a later round does not re-ask and codegen knows the
# case was considered and dismissed.
_ALLOWED_TRANSITIONS: dict[ThreadStatus, frozenset[ThreadStatus]] = {
    "open": frozenset({"answered", "rejected"}),
    "answered": frozenset({"resolved", "open"}),
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


class ThreadMessage(DomainModel):
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
    anchors: tuple[Anchor, ...] = ()
    messages: tuple[ThreadMessage, ...] = ()
    scenario_key: BoardKey | None = None
    evidence: Evidence | None = None

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


class Scenario(DomainModel):
    """A situation the board is asked to account for.

    ``outcomes`` is exactly what ``Board.dry_run`` takes, so a scenario is
    runnable rather than merely described — which is what lets a thread cite a
    trace instead of an opinion.
    """

    key: BoardKey
    kind: Literal["happy", "variant", "probe"]
    description: str
    outcomes: dict[str, str] = Field(default_factory=dict)
    start: BoardKey | None = None
    expected_terminal: BoardKey | None = None
    round: int = 1
