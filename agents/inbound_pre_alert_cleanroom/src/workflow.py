"""The durable shell: signals in, the board walked, one shipment row out.

Every ``meridian`` import — and every module of this agent that imports one —
sits inside the passthrough block. A single import left outside loads the
runtime sandboxed first, after which ``Failure`` from ``check.criteria`` and
``Failure`` from ``runtime`` are two different classes; pydantic rejects one as
not an instance of the other, the workflow task fails, and Temporal retries it
forever. That presents as a hang rather than an error, which is what makes it
expensive to find.

Nothing here reads a clock, calls random or does I/O. The two Checks are pure
and stay in workflow code, which is what makes a failing eval case fail for a
logic reason. The one Action that reaches the world builds its request here and
crosses the activity boundary to perform it.

**Why the walk stops.** The board is a cycle: the certificate Check routes to the
report, and the report routes unconditionally back to the certificate Check. That
edge exists so the four-codes failure path carries on rather than ending — *"we
report it and carry on"* — but it also means a certificate failure would walk
between the two forever. A Check is pure, so re-running it on an unchanged store
must give the same answer; the pass therefore stops the second time it reaches a
Check it has already run. Progress only ever comes from new paperwork, which is
exactly what the spec says: *"When new paperwork lands, the process re-runs the
whole check."*
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy as TemporalRetryPolicy

with workflow.unsafe.imports_passed_through():
    import pydantic_core  # noqa: F401 - pydantic loads it lazily, inside the sandbox
    import spec
    from actions.report_the_discrepancy_to_the_supervisor import (
        report_the_discrepancy_to_the_supervisor,
    )
    from actions.shipment_validated import shipment_validated
    from arrivals import Arrival
    from checks.does_every_batch_have_a_matching_certificate import (
        does_every_batch_have_a_matching_certificate,
    )
    from checks.does_every_invoice_line_carry_all_four_codes import (
        does_every_invoice_line_carry_all_four_codes,
    )
    from findings import Checked, Discrepancy

    from meridian.runtime import EntityStore, RunTrace
    from meridian.runtime.policy import retry_policy
    from meridian.runtime.routing import Routes
    from meridian.runtime.temporal.activities import CapabilityCall, CapabilityResult
    from meridian.runtime.temporal.waits import await_inputs
    from meridian.runtime.tools.bindings import Bindings

EVENT_KEY = "pre_alert_documentation_arrives"
REPORT_KEY = "report_the_discrepancy_to_the_supervisor"

CHECKS = {
    "does_every_invoice_line_carry_all_four_codes": does_every_invoice_line_carry_all_four_codes,
    "does_every_batch_have_a_matching_certificate": does_every_batch_have_a_matching_certificate,
}

# How long the one outward call gets. The card names no timeout, so this is
# chosen rather than specified — long enough for a mail provider on a bad day.
SEND_TIMEOUT = timedelta(seconds=60)

# How many instance names the trace carries per entity. Enough to see which
# documents were read, few enough that a seventeen-certificate shipment stays
# readable.
NAMES_IN_TRACE = 8


@dataclass
class RunRequest:
    """What one shipment's validation is started with.

    ``settle`` is not a deadline from the spec — the Event declares none, and
    inventing one would turn "no time limit" into "expire at once". It is how
    long the caller is willing to hold the instance open once the process has
    stopped making progress, which is a property of the caller rather than of
    the process: production leaves it unset and waits for corrected paperwork,
    while a suite run has a finite corpus and settles at once.
    """

    shipment_no: str
    roles: dict[str, str] = field(default_factory=dict)
    settle: str | None = None
    matched: int = 0
    """How many emails the recognition rule claimed across the whole mailbox."""
    uncorrelated: list[str] = field(default_factory=list)
    """Emails that matched the process and named no shipment.

    Carried in so the trace can say it. A shipment whose row is empty because
    three emails could not be keyed and one whose row is empty because no
    paperwork arrived are the same numbers with entirely different fixes.
    """


@dataclass
class RunResult:
    """The row the process produces, and the trajectory that produced it.

    A dataclass rather than a mapping of ``object``: Temporal's payload
    converter refuses ``object`` and fails at the boundary with a type error
    naming a key rather than a cause. The trace crosses as a JSON string for the
    same reason — a tuple of nested models is not a type the converter takes.
    """

    summary: dict[str, int]
    trace: str


@workflow.defn(name="inbound_pre_alert_validation")
class InboundPreAlertValidation:
    """One shipment, from the first pre-alert to a settled receiving row."""

    def __init__(self) -> None:
        """Start with nothing arrived and nothing reported."""
        self._pending: list[Arrival] = []
        self._store = EntityStore()
        self._documents: dict[str, list[dict[str, Any]]] = {}
        """Merged readings, the authority the store is projected from.

        A document read across two page ranges arrives as two instances, so the
        store cannot be the place they are merged — it appends. These are folded
        as they arrive and the store is rebuilt from them, which keeps the merge
        out of the scaffold's internals.
        """
        self._seen: set[str] = set()
        self._reported: set[str] = set()
        self._summary: dict[str, int] = {}
        self._trace = RunTrace(spec.VERSION, spec.CHECKSUM)
        self._routes = Routes.from_edges(spec.edge_rows())

    @workflow.signal(name="documents_arrived")
    def documents_arrived(self, arrival: Arrival) -> None:
        """Take delivery of one matching email's paperwork.

        ``timing.mode: on_arrival``, so the trigger signals rather than the
        workflow polling. Several emails days apart are one shipment, which is
        why this appends rather than replaces.
        """
        self._pending.append(arrival)

    @workflow.run
    async def run(self, request: RunRequest) -> RunResult:
        """Validate one shipment, reporting discrepancies as they are found."""
        bindings = Bindings(request.roles)
        # The Event declares no deadline, so in production this waits as long as
        # it takes. A caller that knows nothing more is coming supplies a settle
        # window, and when it expires the process runs anyway over an empty
        # store: both Checks then report `on_missing_input`, which is a row
        # saying nothing arrived. A workflow that instead waited forever would
        # reach the suite as a timeout, and a timeout says nothing at all.
        await await_inputs(lambda: bool(self._pending), request.settle)

        while True:
            self._absorb(request)
            if await self._pass(request.shipment_no, bindings):
                break
            if not await await_inputs(lambda: bool(self._pending), request.settle):
                # Nothing further arrived, so re-running the Checks would repeat
                # itself. The row stands as the paperwork on hand makes it.
                break

        return RunResult(summary=dict(self._summary), trace=self._trace.dump())

    async def _pass(self, shipment_no: str, bindings: Bindings) -> bool:
        """Walk the board once over the paperwork on hand.

        Returns:
            Whether the process reached its terminal, and so is finished.
        """
        ran: set[str] = set()
        pending: tuple[Discrepancy, ...] = ()
        step = self._routes.next(EVENT_KEY, "")

        while step:
            if step in CHECKS:
                if step in ran:
                    # Pure, and the store has not changed since it last ran.
                    break
                ran.add(step)
                checked = self._check(step)
                pending = checked.discrepancies
                outcome = checked.result.outcome
            elif step == REPORT_KEY:
                await self._report(step, shipment_no, bindings, pending)
                pending = ()
                outcome = ""
            else:
                self._end_state(step)
                if spec.config(step).get("is_terminal"):
                    return True
                outcome = ""
            step = self._routes.next(step, outcome)

        return False

    def _check(self, key: str) -> Checked:
        """Run one Check and record its counts, its outcome and what failed."""
        with self._trace.step(key) as recorder:
            checked = CHECKS[key](self._store, spec.config(key))
            recorder.produced(checked.result)
        self._summary |= checked.fills
        return checked

    async def _report(
        self, key: str, shipment_no: str, bindings: Bindings, discrepancies: tuple[Discrepancy, ...]
    ) -> None:
        """Tell the supervisor, unless they have already been told this.

        The step is recorded either way. A visit that sent nothing is not the
        same as a visit that never happened, and the difference is the whole
        evidence that deduplication worked rather than that the Check stopped
        failing.
        """
        capability = spec.capability_of(key)
        if capability is None:
            raise RuntimeError(f"{key} declares no capability, so it cannot notify anyone")

        with self._trace.step(key) as recorder:
            report = report_the_discrepancy_to_the_supervisor(
                self._store,
                spec.config(key),
                capability=capability,
                bindings=bindings,
                container_no=shipment_no,
                discrepancies=discrepancies,
                already_reported=frozenset(self._reported),
            )
            if report is None:
                # Two different silences, and a reader has to be able to tell
                # them apart: nothing was wrong, versus everything wrong here
                # was already sent. The second is deduplication working; the
                # first means routing arrived at a report with nothing to say.
                recorder.produced(
                    {
                        "sent": 0,
                        "suppressed": len(discrepancies),
                        "reason": "already reported"
                        if discrepancies
                        else "routed here with no discrepancy to report",
                    }
                )
                return

            answer = await self._invoke(report.call)
            recorder.tool_called(capability, report.call.args, error=answer.error)
            recorder.produced(
                {"sent": 1, "covers": list(report.covers), "shadowed": answer.shadowed}
            )
            self._reported |= set(report.covers)

    async def _invoke(self, call: CapabilityCall) -> CapabilityResult:
        """Cross the activity boundary, which is the only place the world is touched.

        ``result_type`` is passed because the activity is named as a string —
        it belongs to the scaffold, not to this agent — and without it Temporal
        has no signature to read a return type from and hands back a bare dict.
        The first attribute access on that raises inside workflow code, which is
        another hang.
        """
        policy = retry_policy()
        answer: CapabilityResult = await workflow.execute_activity(
            "invoke_capability",
            call,
            result_type=CapabilityResult,
            start_to_close_timeout=SEND_TIMEOUT,
            retry_policy=TemporalRetryPolicy(
                maximum_attempts=policy.max_attempts,
                initial_interval=timedelta(seconds=policy.initial_interval_s),
                backoff_coefficient=policy.backoff,
                non_retryable_error_types=list(policy.non_retryable),
            ),
        )
        return answer

    def _end_state(self, key: str) -> None:
        """Record a step that does nothing but say where the process got to.

        ``is_terminal`` decides whether the walk stops here rather than the name
        of the card doing so: a board may grow a second ``noop`` that is passed
        through on the way somewhere else.
        """
        with self._trace.step(key) as recorder:
            recorder.produced(shipment_validated(spec.config(key)))

    def _absorb(self, request: RunRequest) -> None:
        """Take everything signalled so far into the store, once each.

        A sender resends the whole set rather than a diff, so a shipment that
        wakes three times receives its earlier documents again. Stored per
        arrival, every count becomes a multiple of how many times the process
        woke — wrong in a way that looks plausible, because the numbers stay
        consistent with each other and all of them are inflated equally.

        Nothing on the board declares an identity for an entity, so the content
        is what "the same one" can mean. Extraction runs at temperature zero,
        which is what makes that stable.
        """
        arriving, self._pending = self._pending, []
        for arrival in arriving:
            for instance in arrival.instances:
                fingerprint = _fingerprint(instance.entity, instance.values)
                if fingerprint in self._seen:
                    continue
                self._seen.add(fingerprint)
                self._file(instance.entity, dict(instance.values))
            for gone in arrival.declined:
                self._trace.decline(gone.source, gone.reason)
        self._project()

        with self._trace.step(EVENT_KEY) as recorder:
            recorder.produced(
                {
                    "emails": len(arriving),
                    "matched_in_mailbox": request.matched,
                    "uncorrelated": request.uncorrelated,
                    "instances": self._store.counts(),
                }
                | self._names()
            )

    def _file(self, entity: str, values: dict[str, Any]) -> None:
        """Keep this reading, folded into the document it continues."""
        kept = self._documents.setdefault(entity, [])
        for stored in kept:
            if _same_document(stored, values):
                _combine(stored, values)
                return
        kept.append(values)

    def _project(self) -> None:
        """Rebuild the store from the merged documents.

        Rebuilt rather than mutated in place: ``instances()`` hands back the
        stored mappings themselves, so editing one would work by aliasing the
        scaffold's internals — which is true today and is not a promise it made.
        Re-adding costs nothing at a mailbox's worth of documents.
        """
        rebuilt = EntityStore()
        for entity, documents in self._documents.items():
            for values in documents:
                rebuilt.add(entity, values)
        rebuilt.skipped.extend(self._store.skipped)
        self._store = rebuilt

    def _names(self) -> dict[str, Any]:
        """What each instance is named by, so a count can be checked against a value.

        Counts say a step ran; values say whether it ran on the right thing. The
        scalar fields only — a whole instance would bury the trace it belongs to.
        """
        named: dict[str, Any] = {}
        for entity in self._store.counts():
            rows = [
                {k: v for k, v in row.items() if isinstance(v, str | int | float | bool)}
                for row in self._store.instances(entity)[:NAMES_IN_TRACE]
            ]
            named[f"{entity}.read"] = rows
        return named


def _fingerprint(entity: str, values: dict[str, Any]) -> str:
    """A stable identity for one extracted instance, from its content."""
    return f"{entity}:{json.dumps(values, sort_keys=True, default=str)}"


def _scalars(values: Mapping[str, Any]) -> dict[str, Any]:
    """The fields that name a document, as opposed to the rows it carries."""
    return {
        field: value
        for field, value in values.items()
        if value is not None and not isinstance(value, list | dict) and str(value).strip()
    }


def _same_document(one: Mapping[str, Any], other: Mapping[str, Any]) -> bool:
    """Whether two readings describe one document.

    They do when they agree on every naming field they *both* filled, and there
    is at least one such field. A document is split across page ranges for
    reading, so one reading carries the invoice number and three line items and
    another carries the same number and the next four — comparing whole contents
    made those two invoices, and a shipment reported four where two arrived.

    Requiring a shared field is what keeps this from over-merging: agreeing on
    nothing is not agreement, and two readings that name nothing in common are
    left alone rather than collapsed into one.
    """
    mine, theirs = _scalars(one), _scalars(other)
    shared = mine.keys() & theirs.keys()
    return bool(shared) and all(mine[field] == theirs[field] for field in shared)


def _combine(into: dict[str, Any], addition: Mapping[str, Any]) -> None:
    """Fold a second reading of one document into the first.

    Rows are appended and exact repeats dropped, because a row in both readings
    is one row seen twice while a row in only one is a row the other's pages did
    not cover. Keeping the longer reading instead would discard the rows only
    the shorter one saw — invisibly, because what falls is the count of things a
    Check was meant to examine.
    """
    for name, value in addition.items():
        if isinstance(value, list):
            rows = into.get(name)
            rows = list(rows) if isinstance(rows, list) else []
            seen = {json.dumps(row, sort_keys=True, default=str) for row in rows}
            for row in value:
                stamp = json.dumps(row, sort_keys=True, default=str)
                if stamp not in seen:
                    seen.add(stamp)
                    rows.append(row)
            into[name] = rows
        elif not _scalars(into).get(name) and value is not None:
            into[name] = value
