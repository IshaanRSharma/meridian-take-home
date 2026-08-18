"""Report the COA discrepancy — `effect: notify`, `channel: email`.

Derives `email.send`, so it is an activity. What is written here is the payload
and the reading of the result; the sending is the one generic capability
activity, which is why nothing below names an address or a provider.

`recipients` is a role. `receiving_supervisor` becomes a person at run time
through the bindings file, never in the spec, so a personnel change does not
force a new frozen version.

**Deduplicated by what the message says.** The board is cyclic — a corrected
certificate arrives, the check re-runs, and a supervisor who has already been
told the same two batches are missing would be told again. The card names the
parts that make two reports the same one, and the key is built from those, so
one more batch arriving changes the key and the new discrepancy goes out.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from meridian.runtime.check.paths import resolve
from meridian.runtime.outcome import CheckResult
from meridian.runtime.temporal.activities import CapabilityCall, idempotency_from

KEY = "report_coa_discrepancy"
CAPABILITY = "email.send"


def build_request(
    instances: Mapping[str, Sequence[Mapping[str, Any]]],
    config: Mapping[str, Any],
    result: CheckResult,
    shipment: str,
    recipients: Sequence[str],
) -> CapabilityCall:
    """The message to send, as a request nobody has sent yet.

    `payload_fields` with an iterated path means every value, as a list — the
    SOP asks for the batch numbers, plural, and naming them is the difference
    between a report somebody can act on and a count they have to investigate.
    """
    payload = _payload(instances, config["payload_fields"])
    payload["discrepancy"] = result.outcome
    payload["unmatched"] = _unmatched(result)
    payload["shipment_no"] = shipment

    return CapabilityCall(
        capability=CAPABILITY,
        args={"to": list(recipients), "subject": _subject(shipment), "body": payload},
        idempotency_key=idempotency_from(
            CAPABILITY,
            # The card's own words: container, batches, kind of discrepancy.
            {
                "container_no": shipment,
                "batch_no": payload["unmatched"],
                "discrepancy_kind": result.outcome,
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
    return f"Pre-alert COA discrepancy — {shipment}"


def _unmatched(result: CheckResult) -> list[str]:
    """The batch numbers that matched nothing, as printed, in order found."""
    seen: dict[str, None] = {}
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
