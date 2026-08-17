"""Log the invoice error — `effect: record`, into the customer's own system.

Derives `system.write`, so it is an activity. `system` on the card is free text
— *"Aurologistics WMS"* — because what software a company runs is per customer
and unguessable; which capability that resolves to is the bindings file's job
and never this module's.

The card asks for the invoice number, the drug description and which
information type is missing. The first two are `payload_fields`; the third comes
off the check's failures, where `subject` is the field name that was absent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from meridian.runtime.check.paths import resolve
from meridian.runtime.outcome import CheckResult
from meridian.runtime.temporal.activities import CapabilityCall, idempotency_from

KEY = "report_invoice_discrepancy"
CAPABILITY = "system.write"


def build_request(
    instances: Mapping[str, Sequence[Mapping[str, Any]]],
    config: Mapping[str, Any],
    result: CheckResult,
    shipment: str,
) -> CapabilityCall:
    """The record to write, as a request nobody has written yet."""
    payload = _payload(instances, config["payload_fields"])
    payload["shipment_no"] = shipment
    payload["missing_fields"] = _missing(result)
    payload["line_items_failed"] = result.failed

    return CapabilityCall(
        capability=CAPABILITY,
        args={"system": config.get("system"), "record": payload},
        idempotency_key=idempotency_from(
            CAPABILITY,
            # The card's own words: container, invoice, which field is missing.
            {
                "container_no": shipment,
                "invoice_no": payload.get("invoice_no"),
                "missing_field": payload["missing_fields"],
            },
        ),
    )


def interpret(output: Mapping[str, Any]) -> bool:
    """Whether the record was accepted."""
    return not output.get("error")


def _missing(result: CheckResult) -> list[str]:
    """Which information types were absent, deduplicated, in order found.

    `present` reports the field name as the subject, which is exactly what the
    card means by *which information type is missing*.
    """
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
