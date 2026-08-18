"""The process itself: wait for documents, check them, report, produce the row.

Everything here is deterministic and replayable. The two model calls and the
mailbox live behind `read_documents`; the sends live behind `invoke_capability`.
What is left is decisions, which is exactly what Temporal replays safely.

**Routing is read from the spec, never written here.** `Routes` is built from
`spec.edges()`, so which outcome leads where is data. Adding an edge on the
board changes this workflow's behaviour with no change to this file.

**A repeat edge waits for new documents.** `report_* -> repeat -> check` exists
because corrected paperwork arrives days later and the check has to run again —
not because the check should be retried immediately against the same inputs,
which would spin forever on a discrepancy nobody has fixed yet. So the edge is
followed only when more has arrived since the last pass. Recorded in
assumptions.json, because the board says `repeat` and does not say when.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    import pydantic_core  # noqa: F401  - pydantic loads it lazily, and the sandbox will not
    import spec
    from actions import report_coa_discrepancy, report_invoice_discrepancy
    from activities import Arrival, Gathered, Ingestion
    from checks.coas_valid import coas_valid
    from checks.invoice_complete import invoice_complete

    from meridian.runtime.check.fills import apply_fills
    from meridian.runtime.outcome import Failure
    from meridian.runtime.routing import Routes
    from meridian.runtime.temporal.activities import CapabilityCall, CapabilityResult
    from meridian.runtime.temporal.waits import await_inputs
    from meridian.runtime.trace import RunTrace

ENTRY_STEP = "prealert_received"

MAX_PASSES = 12
"""A ceiling on how many steps one shipment may take.

Not a business rule and not a retry budget — the loop already terminates when
nothing new has arrived. This is the safety net that keeps a mis-drawn cycle
from becoming a workflow that never returns, which Temporal would retry
silently and forever.
"""

_CHECKS = {"invoice_complete": invoice_complete, "coas_valid": coas_valid}


@dataclass
class Input:
    """One shipment, and who to tell about it.

    `recipients` carries roles already resolved to addresses. Identities never
    enter the frozen spec — a personnel change must not force a new version — so
    they arrive as run-time input instead, keyed by the card that names the role.
    """

    shipment: str = ""
    recipients: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Result:
    """What the process produced, and how it got there.

    `row` is JSON rather than named fields because the columns belong to the
    board: they are whatever the checks `fill`, and naming them here would mean
    editing this file every time somebody adds one.
    """

    row: str = "{}"
    trace: str = "{}"
    outcome: str = ""


@workflow.defn(name="InboundPreAlertValidation")
class InboundPreAlertValidation:
    """Validate inbound pre-alert documentation for one shipment."""

    def __init__(self) -> None:
        """Start with nothing arrived and nothing traced.

        Every field is instance state. Module-level mutable state would be
        shared across workflow instances in one worker and invisible inside the
        sandbox, which reloads modules per run.
        """
        self._arrivals: list[Arrival] = []
        self._trace = RunTrace(spec_version=spec.version(), spec_checksum=spec.checksum())
        self._last: dict[str, Any] = {}

    @workflow.signal(name="documents_arrived")
    async def documents_arrived(self, arrival: Arrival) -> None:
        """More paperwork for this shipment.

        Signal-with-start is what makes Thursday's corrected certificate reach
        the instance Tuesday's invoice began, so this is the only way documents
        enter and it may fire at any point in the run.
        """
        self._arrivals.append(arrival)

    @workflow.run
    async def run(self, request: Input) -> Result:
        """Wait for documents, then walk the board until it stops."""
        timing = spec.config(ENTRY_STEP).get("timing") or {}
        if not await await_inputs(lambda: bool(self._arrivals), timing.get("deadline")):
            return Result(row="{}", trace=self._trace.dump(), outcome="timed_out")

        routes = Routes.from_edges(spec.edges())
        repeats = {
            edge["from_key"] for edge in spec.spec()["edges"] if edge.get("relation") == "repeat"
        }

        instances: dict[str, list[dict[str, Any]]] = {}
        row: dict[str, Any] = {}
        read_upto = 0
        step: str | None = routes.next(ENTRY_STEP, "")
        outcome = ""

        for _ in range(MAX_PASSES):
            if step is None:
                break

            if len(self._arrivals) > read_upto:
                instances = await self._gather()
                read_upto = len(self._arrivals)

            outcome = await self._perform(step, instances, row, request)

            if routes.is_terminal(step):
                break

            following = routes.next(step, outcome)
            if following is None:
                break
            if step in repeats and len(self._arrivals) <= read_upto:
                # Nothing new has arrived, so re-running the check would reach
                # the same conclusion and report it again.
                break
            step = following

        # Every check reports, whether or not routing reached it.
        #
        # The board draws an exception exit: a failing invoice routes to
        # `report_invoice_discrepancy`, which is terminal, so `coas_valid` never
        # runs and three columns come back null. The historical output says
        # otherwise — every shipment carries BOTH invoice counts and COA counts,
        # including the ones with invoice failures. So reporting a discrepancy
        # does not end the shipment; it is one finding among several.
        #
        # Decided against the corpus rather than the drawing, and recorded as a
        # spec gap: the board should carry this, and until it is re-frozen the
        # generated code and the drawing disagree on purpose.
        for name in _CHECKS:
            if name not in self._last:
                self._check(name, instances, row)

        _fill_status(row)
        return Result(row=json.dumps(row), trace=self._trace.dump(), outcome=outcome)

    async def _gather(self) -> dict[str, list[dict[str, Any]]]:
        """Read every attachment that has arrived so far."""
        with self._trace.step("read_documents") as recorder:
            gathered: Gathered = await workflow.execute_activity_method(
                Ingestion.read_documents,
                list(self._arrivals),
                start_to_close_timeout=timedelta(minutes=4),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            instances = gathered.entities()
            for source, reason in gathered.skipped():
                self._trace.decline(source, reason)
            # read-vs-kept rather than a bare count: two invoices where one is
            # expected is either two documents or one read twice, and those have
            # opposite fixes. A bare count cannot tell them apart, so a reader
            # has to re-run ingestion to find out what the trace already knew.
            recorder.produced(
                {
                    key: f"{counts['kept']} of {counts['read']} read"
                    for key, counts in gathered.read_and_kept().items()
                }
                # What was actually read off each document, not only how many.
                # A count says extraction ran; the values say whether it read
                # the document in front of it — and "one invoice" and "one
                # invoice whose number is the container number" are the same
                # count and completely different bugs.
                | {f"{key}.read": _identifiers(rows) for key, rows in instances.items()}
            )
        return instances

    async def _perform(
        self,
        step: str,
        instances: dict[str, list[dict[str, Any]]],
        row: dict[str, Any],
        request: Input,
    ) -> str:
        """Run one card, whatever kind it is, and report the outcome it reached."""
        if step in _CHECKS:
            return self._check(step, instances, row)
        return await self._act(step, instances, row, request)

    def _check(
        self, step: str, instances: dict[str, list[dict[str, Any]]], row: dict[str, Any]
    ) -> str:
        """Run a Check in workflow code and write its counts into the row."""
        config = spec.config(step)
        with self._trace.step(step) as recorder:
            result, tallies = _CHECKS[step](instances, config)
            apply_fills(row, config["fills"], tallies, config["scope"], result.failures)
            recorder.produced(
                {
                    "outcome": result.outcome,
                    "total": result.total,
                    "passed": result.passed,
                    "failed": result.failed,
                }
                # The values, not just the count of them. A check reporting
                # "1 failed" sends the reader back to the documents; one
                # reporting which value failed and what it was matched against
                # is the diagnosis, and the two cost the same to record.
                | _evidence(result.failures)
            )
        self._last[step] = result
        return result.outcome

    async def _act(
        self,
        step: str,
        instances: dict[str, list[dict[str, Any]]],
        row: dict[str, Any],
        request: Input,
    ) -> str:
        """Run an Action, crossing the activity boundary only if it has to."""
        config = spec.config(step)
        with self._trace.step(step) as recorder:
            if not spec.card(step)["capabilities"]:
                # `effect: noop`. A named end state, and nothing to send.
                recorder.produced({"terminal": bool(config.get("is_terminal"))})
                return ""

            call = self._request(step, config, instances, row, request)
            answer: CapabilityResult = await workflow.execute_activity(
                "invoke_capability",
                call,
                # Named as a string because the activity belongs to the scaffold
                # rather than this agent, which means Temporal has no signature
                # to read a return type from and hands back a bare dict unless
                # it is told one.
                result_type=CapabilityResult,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            recorder.tool_called(
                call.capability, call.args, shadowed=answer.shadowed, error=answer.error
            )
            recorder.produced({"ok": answer.ok, "shadowed": answer.shadowed})
        return ""

    def _request(
        self,
        step: str,
        config: dict[str, Any],
        instances: dict[str, list[dict[str, Any]]],
        row: dict[str, Any],  # noqa: ARG002 - kept so every action takes one shape
        request: Input,
    ) -> CapabilityCall:
        """Build one Action's request, purely, from its own module."""
        result = self._last[_reported_by(step)]
        if step == report_coa_discrepancy.KEY:
            return report_coa_discrepancy.build_request(
                instances, config, result, request.shipment, request.recipients.get(step, [])
            )
        return report_invoice_discrepancy.build_request(instances, config, result, request.shipment)


def _reported_by(action: str) -> str:
    """Which check's result this report describes.

    Read off the board rather than listed: an exception edge runs from the check
    that failed to the action that reports it, so the edge already says which
    failures the report is about. Listing the pairs here would be a second copy
    of the graph, wrong the first time somebody redraws an edge.
    """
    for edge in spec.spec()["edges"]:
        if edge["to_key"] == action and edge["from_key"] in _CHECKS:
            return str(edge["from_key"])
    msg = f"no check leads to {action!r}, so there are no failures for it to report"
    raise LookupError(msg)


def _fill_status(row: dict[str, Any]) -> None:
    """Whether the shipment is still open, derived from what the checks found.

    ACTIVE when any check reported a failure, RESOLVED otherwise. No card on the
    board fills this column, so it is the loop's decision and not the process
    owner's — raised as a `spec_gap` thread rather than quietly written.

    The rule fits every historical row (9/9), and one of them is what rules it
    out being about ASN: MNBU3974949 carries 14 mismatched ASNs and is RESOLVED,
    so an ASN mismatch does not hold a shipment open.
    """
    failures = (
        int(row.get("invoices_failed") or 0)
        + int(row.get("goods_failed") or 0)
        + int(row.get("failed_coa") or 0)
    )
    row["status"] = "ACTIVE" if failures else "RESOLVED"


def _identifiers(instances: Sequence[Mapping[str, Any]], most: int = 6) -> list[dict[str, Any]]:
    """What names each thing that was read, without its whole contents.

    Scalars only, so an invoice shows its number rather than every line item on
    it. A trace nobody reads is worth as little as no trace, and one shipment in
    the corpus carries eleven invoices and fourteen certificates.
    """
    named: list[dict[str, Any]] = []
    for instance in instances[:most]:
        named.append(
            {
                field: value
                for field, value in sorted(instance.items())
                if value is not None and not isinstance(value, list | dict) and str(value).strip()
            }
        )
    if len(instances) > most:
        named.append({"and": f"{len(instances) - most} more"})
    return named


def _evidence(failures: Sequence[Failure], most: int = 8) -> dict[str, Any]:
    """The values that failed, and the values they were matched against.

    Both halves, because either alone is half a diagnosis. `UCB26016A` failing
    means nothing until you see the certificates offered `UCB26016` — at which
    point the trailing letter explains itself and nobody has to open a PDF.
    """
    failing = list(dict.fromkeys(f.subject for f in failures if f.subject))
    if not failing:
        return {}

    offered: list[str] = []
    for failure in failures:
        for candidate in failure.detail.get("available") or ():
            if str(candidate) not in offered:
                offered.append(str(candidate))

    found: dict[str, Any] = {"failing": failing[:most]}
    if len(failing) > most:
        found["failing_more"] = len(failing) - most
    if offered:
        found["available"] = offered[:most]
    return found
