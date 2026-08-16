"""The immutable spec: what a code generator is handed and nothing more.

The board is rows and keeps changing. A spec is one snapshot, checksummed, and
inert after codegen. That inertness is the point — a repair loop editing a
schema can only fix what the schema anticipated, while a loop editing code can
restructure.

Each primitive arrives with its context already inlined. A statement anchored at
board level physically repeats on every primitive that inherits it, which is the
correct trade: codegen reads one entry and has everything, with no lookups and
no joins.

What a spec deliberately does not carry: the questions that produced it, sibling
statements destined for other files, anyone's name or address, and any tool
name. Identities live in bindings so a personnel change never forces a new spec
version, and capabilities stay abstract so the same spec deploys to a customer
running different software.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from meridian.domain.graph import Edge
from meridian.domain.primitives import (
    ActionConfig,
    BoardKey,
    CheckConfig,
    DomainModel,
    EntityConfig,
    EventConfig,
)


class ScopedContext(DomainModel):
    """Everything settled that bears on one primitive.

    ``inherited`` was anchored more broadly and reaches here through the scope
    chain; ``local`` was anchored on this primitive. Narrow beats broad on
    conflict, the same convention as lexical scope.

    ``negative`` is collected at every level and never overridden, because it
    states something about the world rather than about a step — "expiry is not
    checked at pre-alert" does not stop being true lower down.
    """

    inherited: tuple[str, ...] = ()
    local: tuple[str, ...] = ()
    negative: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        """Whether review settled nothing that bears on this primitive."""
        return not (self.inherited or self.local or self.negative)


class SpecPrimitive(DomainModel):
    """One card as codegen receives it: its config, its tools, and what it means."""

    key: BoardKey
    primitive_type: Literal["event", "action", "check"]
    config: EventConfig | ActionConfig | CheckConfig
    capabilities: tuple[str, ...] = ()
    context: ScopedContext = ScopedContext()

    def is_activity(self) -> bool:
        """Whether this compiles to a Temporal activity rather than inline code.

        Anything touching the world is an activity and anything deciding is
        workflow code, so a non-empty capability list is exactly the boundary.
        """
        return bool(self.capabilities)


class FrozenSpec(DomainModel):
    """A board, settled and sealed.

    The checksum covers everything except itself and the timestamp, so two
    freezes of an unchanged board agree and any later edit is detectable.
    """

    version: int = 1
    board_id: UUID | None = None
    checksum: str = ""
    frozen_at: datetime | None = None
    entities: dict[str, EntityConfig] = Field(default_factory=dict)
    primitives: dict[str, SpecPrimitive] = Field(default_factory=dict)
    edges: tuple[Edge, ...] = ()
    # A statement about a transition — "wait 48 hours, then escalate" — reaches
    # no card, because a claim about an edge does not belong inside a step's
    # file. Without a home here it would be lost at the freeze.
    edge_context: dict[str, ScopedContext] = Field(default_factory=dict)
    capabilities: tuple[str, ...] = ()

    def payload(self) -> str:
        """The canonical bytes the checksum is taken over.

        Sorted keys and no whitespace, so the digest depends on content rather
        than on how the JSON happened to be serialised.
        """
        body = self.model_dump(mode="json", exclude={"checksum", "frozen_at"})
        return json.dumps(body, sort_keys=True, separators=(",", ":"))

    def compute_checksum(self) -> str:
        """The digest this spec's content implies."""
        return hashlib.sha256(self.payload().encode()).hexdigest()

    def sealed(self) -> FrozenSpec:
        """A copy carrying its own checksum."""
        return self.model_copy(update={"checksum": self.compute_checksum()})

    def is_intact(self) -> bool:
        """Whether the content still matches the checksum it was sealed with."""
        return bool(self.checksum) and self.checksum == self.compute_checksum()

    def activities(self) -> tuple[SpecPrimitive, ...]:
        """Primitives that reach the world, in key order."""
        return tuple(p for _, p in sorted(self.primitives.items()) if p.is_activity())
