"""Tell the receiving supervisor what is wrong, once per distinct problem.

``effect: notify`` with ``channel: email``, so this reaches the world — but only
through the capability the card declares, and only ever with a role name. The
supervisor's address is resolved from bindings at run time and never appears in
this file, which is what lets the same spec deploy to a second customer.

The card is cyclic by construction: corrected paperwork arrives, the checks
re-run, and a supervisor who has already been told would be told again. Three
settled statements decide the shape of that:

* *"The supervisor gets one email for the whole shipment, and that single
  message lists every affected invoice number and batch number together."* —
  one message per visit, not one per batch.
* *"a corrected line only stops failing and does not create a new report for the
  same invoice and batch problem"* — the identity of a report is the problem,
  not the pass that found it.
* *"the supervisor sees one email per distinct discrepancy rather than one per
  re-check"* — so a visit with nothing new to say sends nothing at all.

The card's ``idempotency_key`` names the three parts of that identity in the
process owner's own words — ``container_no + batch_no + discrepancy_kind`` — and
this file builds exactly those three into the key the activity deduplicates on.

Nothing here performs the send. It returns the request; the workflow crosses the
activity boundary with it, which keeps this file network-free and therefore the
file a repair can change without a server.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from checking import Place, rows_for
from findings import Discrepancy

from meridian.runtime import Bindings, EntityStore
from meridian.runtime.temporal.activities import CapabilityCall, idempotency_from

_ITERATED = "[]"


@dataclass(frozen=True)
class Fresh:
    """One not-yet-reported discrepancy, with the two things that identify it."""

    discrepancy: Discrepancy
    subject: str
    """The value that names it -- the ``batch_no`` half of the card's key."""
    identity: str


@dataclass(frozen=True)
class Report:
    """One email to send, and the problems it settles."""

    call: CapabilityCall
    covers: tuple[str, ...]
    """The report identities this message discharges, so a re-check stays quiet."""


def report_the_discrepancy_to_the_supervisor(  # noqa: PLR0913 - the card names all six
    store: EntityStore,
    card: Mapping[str, Any],
    *,
    capability: str,
    bindings: Bindings,
    container_no: str,
    discrepancies: Sequence[Discrepancy],
    already_reported: frozenset[str],
) -> Report | None:
    """Build the one message this visit owes the supervisor, or nothing.

    Args:
        store: every entity instance gathered for this shipment.
        card: this primitive's ``config`` from the frozen spec.
        capability: the key the card declares for reaching the world.
        bindings: resolves a role to whoever fills it for this customer.
        container_no: the correlation key, and the first part of a report's identity.
        discrepancies: what the Check that routed here found.
        already_reported: identities the supervisor has been told about.

    Returns:
        The request to send, or ``None`` when every problem is already known.
    """
    fresh = _unreported(store, card, container_no, discrepancies, already_reported)
    if not fresh:
        return None

    kinds = sorted({one.discrepancy.kind for one in fresh})
    places = {one.discrepancy.place for one in fresh}
    payload = {
        str(field["path"]): _values_at(store, field, places) for field in card["payload_fields"]
    }
    body = _body(container_no, kinds, payload, [one.discrepancy for one in fresh])

    return Report(
        call=CapabilityCall(
            capability=capability,
            args={
                "to": [bindings.role(str(who["role"])) for who in card["recipients"]],
                "subject": f"Pre-alert discrepancy — container {container_no}",
                "body": body,
            },
            # `container_no + batch_no + discrepancy_kind`, straight off the card.
            # The key changes exactly when the set of problems does, so a new
            # batch failing sends a new email and a re-check of the same two
            # batches sends nothing.
            idempotency_key=idempotency_from(
                capability,
                {
                    "container_no": container_no,
                    "batch_no": sorted({one.subject for one in fresh}),
                    "discrepancy_kind": kinds,
                },
            ),
        ),
        covers=tuple(one.identity for one in fresh),
    )


def _unreported(
    store: EntityStore,
    card: Mapping[str, Any],
    container_no: str,
    discrepancies: Sequence[Discrepancy],
    already_reported: frozenset[str],
) -> list[Fresh]:
    """Each discrepancy with its identity, minus the ones already sent.

    The identity is the card's own key -- container, batch, kind -- so two
    passes finding the same problem produce the same string and the second one
    sends nothing.
    """
    subjects = _subjects(store, card)
    seen: set[str] = set()
    fresh: list[Fresh] = []
    for one in discrepancies:
        subject = subjects.get(one.place) or _fallback(one.place)
        identity = f"{container_no}|{subject}|{one.kind}"
        if identity in already_reported or identity in seen:
            continue
        seen.add(identity)
        fresh.append(Fresh(discrepancy=one, subject=subject, identity=identity))
    return fresh


def _subjects(store: EntityStore, card: Mapping[str, Any]) -> dict[Place, str]:
    """The value that names each place, taken from the card's iterated payload field.

    A scalar payload field names the document; an iterated one names the row
    inside it, which is the ``batch_no`` half of the card's own idempotency key.
    Reading it off the card rather than naming the path keeps the two in step.
    """
    named: dict[Place, str] = {}
    for field in card["payload_fields"]:
        if _ITERATED not in str(field["path"]):
            continue
        for row in rows_for(store, field):
            if row.value is not None and str(row.value).strip():
                named[(row.document, row.indices)] = str(row.value)
    return named


def _fallback(place: Place) -> str:
    """How to name a line item that carries no value of its own.

    A line item missing its batch number still has to be reportable, and it has
    to keep a *stable* identity so a re-check does not send a second email about
    it. Its position is the only thing left that is stable.
    """
    document, indices = place
    return "line " + ".".join(str(n) for n in (document, *indices))


def _values_at(
    store: EntityStore, field: Mapping[str, Any], places: set[Place]
) -> list[str]:
    """Every value of one payload field at the places that failed.

    ``payload_fields`` with an iterated path means every value, as a list — and
    the card narrows *which* values: the message lists every **affected** invoice
    number and batch number, not every one on the shipment.
    """
    documents = {document for document, _ in places}
    values = [
        str(row.value)
        for row in rows_for(store, field)
        if row.value is not None
        and str(row.value).strip()
        and (
            (row.document, row.indices) in places
            or (row.indices == () and row.document in documents)
        )
    ]
    return list(dict.fromkeys(values))


def _body(
    container_no: str,
    kinds: Sequence[str],
    payload: Mapping[str, Sequence[str]],
    discrepancies: Sequence[Discrepancy],
) -> str:
    """The message, naming what is wrong rather than counting it.

    The SOP asks for the invoice number, the batch numbers and a description of
    the discrepancy, and the description is the Check's own ``evidence`` — which
    is why a supervisor reading this does not have to open the invoice.
    """
    lines = [f"Container {container_no} has documentation discrepancies."]
    for path, values in payload.items():
        if values:
            lines.append(f"{path.rsplit('.', 1)[-1]}: {', '.join(values)}")
    lines.append(f"kind: {', '.join(kinds)}")
    lines.append("")
    lines.extend(f"- {one.kind}: {one.evidence or _fallback(one.place)}" for one in discrepancies)
    return "\n".join(lines)
