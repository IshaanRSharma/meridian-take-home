"""What crosses the signal boundary when paperwork turns up.

The correlation key is ``commercial_invoice.container_no`` — a field on a
document inside an attachment — so nothing can decide *which* shipment an email
belongs to until the invoice has been read. Reading a PDF and calling a model is
I/O, and I/O above the activity boundary breaks replay, so extraction happens in
the trigger and the workflow is signalled with instances rather than with files.

That is forced by the spec rather than chosen: a workflow keyed on a value only
extraction can produce cannot be the thing that does the extracting.

Plain dataclasses because Temporal's payload converter takes concrete types, and
because a signal argument outlives the signature it was first given — a field
added here does not break instances already in flight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Instance:
    """One entity instance, as extraction produced it."""

    entity: str
    values: dict[str, Any] = field(default_factory=dict)


@dataclass
class Declination:
    """Something that arrived and is not part of this process, and why.

    Carried through to the trace rather than dropped: *"found no certificates"*
    and *"skipped the certificates"* are the same empty result with entirely
    different fixes, and only one of them is a bad recognition rule.
    """

    source: str
    reason: str


@dataclass
class Arrival:
    """One matching email's worth of paperwork, already recognised and read."""

    message_id: str
    subject: str = ""
    instances: list[Instance] = field(default_factory=list)
    declined: list[Declination] = field(default_factory=list)
