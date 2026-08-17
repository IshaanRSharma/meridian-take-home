"""Where the data a process reads actually lives.

The missing middle. ``check/paths.resolve`` consumes instances of an entity and
nothing produced them; this is what produces them.

Three routes in, and a scaffold assuming only the first cannot run half the
processes the vocabulary describes:

    extracted   read off a document that arrived        pre-alert
    looked up   returned by an Action with effect=lookup  credentialing
    filled      accumulated by Checks as they run         the output row

They differ in where the data comes from and in nothing else, so the store does
not distinguish them. What it does record is what it **declined** — an
attachment matching no recognition rule is skipped, because the SOP says
*locate* the invoice among the attachments, but a skipped file and an absent one
produce the same empty result from very different causes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Skipped:
    """Something that arrived and matched nothing the process declared."""

    source: str
    reason: str


@dataclass
class EntityStore:
    """Every instance of every entity, for one case.

    Mutable and instance-scoped: one store per workflow execution, never module
    state, so the runtime stays safe to pass through the Temporal sandbox.
    """

    _instances: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    skipped: list[Skipped] = field(default_factory=list)

    def add(self, entity: str, instance: Mapping[str, Any]) -> None:
        """Record one instance of an entity, however it was obtained."""
        self._instances.setdefault(entity, []).append(dict(instance))

    def decline(self, source: str, reason: str) -> None:
        """Record something that arrived and is not part of this process.

        Never silent. "Found no invoices" and "skipped six files, one of which
        was the invoice" are the same empty result with entirely different
        fixes, and only one of them is a bad recognition rule.
        """
        self.skipped.append(Skipped(source=source, reason=reason))

    def instances(self, entity: str) -> Sequence[Mapping[str, Any]]:
        """Every instance of one entity, in arrival order.

        Empty rather than raising: a certificate that never turned up is a
        finding the process is meant to report, not a crash.
        """
        return tuple(self._instances.get(entity, ()))

    def counts(self) -> dict[str, int]:
        """How many of each arrived. The first line of any extraction failure."""
        return {entity: len(rows) for entity, rows in sorted(self._instances.items())}
