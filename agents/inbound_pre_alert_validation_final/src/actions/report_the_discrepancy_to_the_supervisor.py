"""Report the discrepancy — `effect: notify`, `channel: email`.

Derives `email.send`, so it is an activity. What is written here is the payload
and the reading of the result; the sending is the scaffold's one generic
capability activity, which is why nothing below names an address or a provider.

`recipients` is a role. `receiving_supervisor` becomes a person at run time
through the bindings file, never in the spec, so a personnel change does not
force a new frozen version.

**One email for the whole shipment.** Review settled this on the card:

    [rule] The supervisor gets one email for the whole shipment, and that single
           message lists every affected invoice number and batch number together.

So this takes *every* check's failures rather than one check's, and the workflow
calls it once after the walk rather than each time routing reaches it. Two
invoices failing and a missing certificate is one message naming all of it — the
process owner's reason being that they work a container at a time and chase one
clearing agent about it, so three emails is three times the noise.

    [rule] The supervisor is chased the same way for a missing certificate and
           for a certificate that names a different batch.

Which is why there is one `discrepancy` list and not one per kind.

**Deduplicated by what the message says.** The card names `container_no +
batch_no + discrepancy_kind`, and `idempotency_from` hashes exactly that — so a
re-check finding the same problems produces the same key and the supervisor is
not told twice, while one more batch arriving changes the payload, changes the
key, and the new discrepancy goes out. That is the settled negative: the earlier
report is not retracted or replaced, and the same invoice and batch stay one
issue.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from meridian.runtime.check.paths import resolve
from meridian.runtime.outcome import CheckResult
from meridian.runtime.temporal.activities import CapabilityCall, idempotency_from

KEY = "report_the_discrepancy_to_the_supervisor"
CAPABILITY = "email.send"


def build_request(
    instances: Mapping[str, Sequence[Mapping[str, Any]]],
    config: Mapping[str, Any],
    found: Mapping[str, CheckResult],
    shipment: str,
    recipients: Sequence[str],
) -> CapabilityCall:
    """The message to send, as a request nobody has sent yet.

    `found` is every check that ran, keyed by card, rather than the one check
    that routed here — because the settled rule is one message listing
    everything, and a single result could only ever describe half of it.

    `payload_fields` with an iterated path means every value, as a list. The SOP
    asks for the batch numbers, plural, and naming them is the difference
    between a report somebody can act on and a count they have to investigate.
    """
    payload = _payload(instances, config["payload_fields"])
    payload["shipment_no"] = shipment
    payload["discrepancies"] = _discrepancies(found)
    payload["affected"] = _affected(found)

    return CapabilityCall(
        capability=CAPABILITY,
        args={"to": list(recipients), "subject": _subject(shipment), "body": payload},
        idempotency_key=idempotency_from(
            CAPABILITY,
            # The card's own words: container, batches, kind of discrepancy.
            {
                "container_no": shipment,
                "batch_no": payload["affected"],
                "discrepancy_kind": payload["discrepancies"],
            },
        ),
    )


def interpret(output: Mapping[str, Any]) -> bool:
    """Whether the report actually went out.

    A shadowed call is a success: nothing left the building, and the eval
    measures the row the process produced rather than a delivered email.
    """
    return not output.get("error")


def _subject(shipment: str) -> str:
    return f"Pre-alert documentation discrepancy — {shipment}"


def _discrepancies(found: Mapping[str, CheckResult]) -> list[str]:
    """Which checks came out badly, named by the outcome the card declared.

    The outcome literal, never a phrase of this module's own, so a reader
    grepping `missing_coa` finds the card, the edge and this message.
    """
    return [
        result.outcome for result in found.values() if result.failed
    ]


def _affected(found: Mapping[str, CheckResult]) -> list[str]:
    """Everything that failed, across every check, in the order found.

    Deduplicated but never sorted: the order a reader sees is the order the
    documents were read, which is the order they will look through them in.
    """
    seen: dict[str, None] = {}
    for result in found.values():
        for failure in result.failures:
            if failure.subject:
                seen.setdefault(failure.subject, None)
    return list(seen)


def _payload(
    instances: Mapping[str, Sequence[Mapping[str, Any]]], fields: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Resolve each declared payload field, scalar or list, by its path."""
    built: dict[str, Any] = {}
    for field in fields:
        rows = resolve(field["entity"], instances.get(field["entity"], ()), field["path"])
        name = str(field["path"]).rsplit(".", 1)[-1]
        values = [row.value for row in rows if row.value is not None]
        built[name] = values if "[]" in str(field["path"]) else next(iter(values), None)
    return built
