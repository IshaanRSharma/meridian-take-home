"""The process itself: wait for documents, check them, report once, produce the row.

Everything here is deterministic and replayable. The two model calls and the
mailbox live behind `read_documents`; the send lives behind `invoke_capability`.
What is left is decisions, which is exactly what Temporal replays safely.

**Routing is read from the spec, never written here.** `Routes` is built from
`spec.edges()`, so which outcome leads where is data, and redrawing an edge on
the board changes this workflow with no change to this file.

**Two things about this board's shape are worth knowing before reading the walk.**

The graph is genuinely cyclic. `report -> does_every_batch_have_a_matching_certificate`
exists so that a failing invoice still reaches the certificate check, and
`does_every_batch_have_a_matching_certificate -missing_coa-> report` exists so a
missing certificate is reported. Together those two edges are a loop, and a walk
that followed them literally would never return. Each step therefore runs at
most once — which is not a workaround but the settled rule showing up in the
traversal, because the process owner said the supervisor gets *one* email.

And the send is deferred to the end. Routing reaches the report from either
check, but review settled that both belong in a single message listing every
affected invoice and batch together; a report sent the moment routing arrived
could only ever describe the half of the shipment checked so far.
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
    from actions import report_the_discrepancy_to_the_supervisor as report
    from checks.does_every_batch_have_a_matching_certificate import (
        does_every_batch_have_a_matching_certificate,
    )
    from checks.does_every_invoice_line_carry_all_four_codes import (
        does_every_invoice_line_carry_all_four_codes,
    )
    from ingestion import Arrival, Gathered, Ingestion

    from meridian.runtime.check.fills import apply_fills
    from meridian.runtime.outcome import CheckResult, Failure
    from meridian.runtime.routing import Routes
    from meridian.runtime.temporal.activities import CapabilityCall, CapabilityResult
    from meridian.runtime.temporal.waits import await_inputs
    from meridian.runtime.trace import RunTrace

ENTRY_STEP = "pre_alert_documentation_arrives"

MAX_PASSES = 12
"""A ceiling on how many steps one shipment may take.

Not a business rule and not a retry budget — the walk already terminates,
because no step is entered twice. This is the safety net that keeps a redrawn
board from becoming a workflow that never returns, which Temporal would retry
silently and forever.
"""

_CHECKS = {
    "does_every_invoice_line_carry_all_four_codes": does_every_invoice_line_carry_all_four_codes,
    "does_every_batch_have_a_matching_certificate": does_every_batch_have_a_matching_certificate,
}


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


@workflow.defn(name="InboundPreAlertValidationFinal")
class InboundPreAlertValidationFinal:
    """Validate inbound pre-alert documentation for one shipment."""

    def __init__(self) -> None:
        """Start with nothing arrived and nothing traced.

        Every field is instance state. Module-level mutable state would be
        shared across workflow instances in one worker and invisible inside the
        sandbox, which reloads modules per run.
        """
        self._arrivals: list[Arrival] = []
        self._trace = RunTrace(spec_version=spec.version(), spec_checksum=spec.checksum())
        self._found: dict[str, CheckResult] = {}

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
        """Wait for documents, walk the board, then report once."""
        timing = spec.config(ENTRY_STEP).get("timing") or {}
        if not await await_inputs(lambda: bool(self._arrivals), timing.get("deadline")):
            return Result(row="{}", trace=self._trace.dump(), outcome="timed_out")

        routes = Routes.from_edges(spec.edges())
        instances = await self._gather()
        row: dict[str, Any] = {}

        step: str | None = routes.next(ENTRY_STEP, "")
        visited: set[str] = set()
        outcome = ""
        reportable = False

        for _ in range(MAX_PASSES):
            if step is None or step in visited:
                # Already run. The board's two report edges form a real cycle,
                # and one pass over each step is what the settled "one email"
                # rule means when it reaches the traversal.
                break
            visited.add(step)

            if step in _CHECKS:
                outcome = self._check(step, instances, row)
            elif step == report.KEY:
                # Reached, not sent. The message has to name everything, and
                # half the checks have not run yet.
                reportable = True
                outcome = ""
            else:
                outcome = self._end(step)

            if routes.is_terminal(step):
                break
            step = routes.next(step, outcome)

        # Every check fills part of the row, so a check the walk never reached
        # would leave columns absent and the row is arithmetic. This board's
        # edges reach both from every path, so this currently never fires — it
        # is here because a redrawn edge must not silently shorten the row.
        for name in _CHECKS:
            if name not in self._found:
                self._check(name, instances, row)

        if reportable:
            await self._report(instances, row, request)

        return Result(row=json.dumps(row), trace=self._trace.dump(), outcome=outcome)

    async def _gather(self) -> dict[str, list[dict[str, Any]]]:
        """Read every attachment that has arrived so far."""
        with self._trace.step("read_documents") as recorder:
            gathered: Gathered = await workflow.execute_activity_method(
                Ingestion.read_documents,
                list(self._arrivals),
                start_to_close_timeout=timedelta(minutes=8),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            instances = gathered.entities()
            for source, reason in gathered.skipped():
                self._trace.decline(source, reason)
            # Read-versus-kept rather than a bare count: two invoices where one
            # is expected is either two documents or one document read twice,
            # and those have opposite fixes.
            recorder.produced(
                {
                    key: f"{counts['kept']} of {counts['read']} read"
                    for key, counts in gathered.read_and_kept().items()
                }
                | {f"{key}.read": _identifiers(rows) for key, rows in instances.items()}
            )
        return instances

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
                | _evidence(result.failures)
            )
        self._found[step] = result
        return result.outcome

    def _end(self, step: str) -> str:
        """A named end state. `effect: noop`, so there is nothing to send."""
        config = spec.config(step)
        with self._trace.step(step) as recorder:
            recorder.produced({"terminal": bool(config.get("is_terminal"))})
        return ""

    async def _report(
        self,
        instances: dict[str, list[dict[str, Any]]],
        row: dict[str, Any],  # noqa: ARG002 - the row is the output, never the message
        request: Input,
    ) -> None:
        """Send the one message this shipment gets, naming everything found."""
        step = report.KEY
        config = spec.config(step)
        with self._trace.step(step) as recorder:
            call: CapabilityCall = report.build_request(
                instances,
                config,
                self._found,
                request.shipment,
                request.recipients.get(step, []),
            )
            answer: CapabilityResult = await workflow.execute_activity(
                "invoke_capability",
                call,
                # Named as a string because the activity belongs to the scaffold
                # rather than this agent, which leaves Temporal no signature to
                # read a return type from — it hands back a bare dict unless told.
                result_type=CapabilityResult,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            recorder.tool_called(
                call.capability, call.args, shadowed=answer.shadowed, error=answer.error
            )
            recorder.produced({"ok": report.interpret(answer.output), "shadowed": answer.shadowed})


def _identifiers(instances: Sequence[Mapping[str, Any]], most: int = 6) -> list[dict[str, Any]]:
    """What names each thing that was read, without its whole contents.

    Scalars only, so an invoice shows its number rather than every line item on
    it. A trace nobody reads is worth as little as no trace, and one shipment in
    this corpus carries eleven invoices and fourteen certificates.
    """
    named: list[dict[str, Any]] = []
    for instance in instances[:most]:
        named.append(
            {
                name: value
                for name, value in sorted(instance.items())
                if value is not None and not isinstance(value, list | dict) and str(value).strip()
            }
        )
    if len(instances) > most:
        named.append({"and": f"{len(instances) - most} more"})
    return named


def _evidence(failures: Sequence[Failure], most: int = 8) -> dict[str, Any]:
    """The values that failed, and the values they were matched against.

    Both halves, because either alone is half a diagnosis. `FI5026009A` failing
    means nothing until you see the certificates offered `FI5026007` and
    `FI5026008` — at which point the reader knows a certificate is genuinely
    absent rather than spelled differently, and nobody has to open a PDF.
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
