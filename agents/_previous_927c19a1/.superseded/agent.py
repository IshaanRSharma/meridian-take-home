"""Inbound pre-alert validation, as a Temporal workflow.

Attachments arrive as signals, ingestion classifies and extracts them, the two
checks count, fills project the counts into the output row, and routing walks
the graph. Everything process-specific comes out of `spec.lock.json`.

The entry point the platform calls is `run_case`, at the bottom. It owns the
Temporal setup — which signals, how many arrivals, which providers — because
those are facts about *this* process, and a harness that knew them would be a
harness for one agent.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

# EVERY meridian import goes inside this block, and so do the two check modules
# — they import meridian themselves, and a module the sandbox reloads gets a
# second copy of `Failure` that pydantic rejects as not being the first. That
# presents as a HANG rather than an error, because Temporal retries a failed
# workflow task forever.
with workflow.unsafe.imports_passed_through():
    import pydantic_core  # noqa: F401 - pydantic loads it lazily, inside the sandbox

    from checks.coas_valid import coas_valid
    from checks.invoice_complete import invoice_complete

    from meridian.runtime import CheckResult, RunTrace
    from meridian.runtime.entities import EntityStore
    from meridian.runtime.harness import CaseOutcome
    from meridian.runtime.ingest import UNRECOGNISED, Candidate, Pipeline, Source, Verdict, ingest
    from meridian.runtime.routing import Routes
    from meridian.runtime.temporal.activities import Capabilities, CapabilityCall
    from meridian.runtime.temporal.waits import await_inputs
    from meridian.runtime.tools.dispatch import Tool, Tools
    from meridian.runtime.tools.providers import RecordingProvider

HERE = pathlib.Path(__file__).parent
SPEC = json.loads((HERE / "spec.lock.json").read_text())

# The output entity: the one no Event captures and no Action produces, written
# only by fills. It is the workflow's return value.
OUTPUT = "shipment_summary"


@dataclass
class Arrival:
    """One attachment turning up.

    Carries its already-extracted fields as a JSON string on the signal rather
    than in a module global: the sandbox reloads modules, so anything set from
    outside the workflow is invisible inside it.
    """

    ref: str
    name: str
    text: str
    payload: str = "[]"


@dataclass
class Input:
    shipment: str
    expected_sources: int = 1
    deadline: str | None = "PT48H"


@dataclass
class Row:
    """The eval row, plus the trace.

    Concrete types only — Temporal's payload converter refuses `object` and
    fails at the boundary naming a key rather than a cause. The trace crosses as
    a string for the same reason.
    """

    shipment_no: str
    invoices_successful: int = 0
    invoices_failed: int = 0
    invoices_total: int = 0
    goods_failed: int = 0
    failed_coa: int = 0
    coa_success: int = 0
    coa_total: int = 0
    status: str = "ACTIVE"
    steps: list[str] = field(default_factory=list)
    trace: str = "{}"


# --- ingestion, driven entirely by the spec -----------------------------------


@dataclass(frozen=True)
class Arrived:
    """A reader over what the signals carried. Instance state, never module state."""

    text: dict[str, str]

    def read(self, source: Source) -> str:
        return self.text[source.ref]


def classify(text: str, candidates: Any) -> Verdict:
    """Recognise a page by the phrase the process owner quoted.

    `identified_by` is PROSE — it describes how to recognise the document, not a
    byte comparison — so the match is case-insensitive. See `assumptions.json`.
    """
    for candidate in candidates:
        parts = candidate.identified_by.split('"')
        if len(parts) > 1 and parts[1].lower() in text.lower():
            return Verdict(candidate.entity, 1.0)
    return Verdict(UNRECOGNISED, 1.0)


def make_extractor(payloads: dict[str, list[dict[str, Any]]]) -> Any:
    """An extractor closed over what arrived, rather than reading a global."""

    def extract(text: str, candidate: Candidate) -> list[dict[str, Any]]:
        return payloads.get(candidate.entity, [])

    return extract


@workflow.defn
class InboundPreAlert:
    """Wait for the paperwork, check it, report what is missing."""

    def __init__(self) -> None:
        self.arrivals: list[Arrival] = []

    @workflow.signal
    def documents_arrived(self, arrival: Arrival) -> None:
        self.arrivals.append(arrival)

    @workflow.run
    async def run(self, case: Input) -> Row:
        trace = RunTrace(spec_version=SPEC["version"], spec_checksum=SPEC["checksum"])
        await await_inputs(lambda: len(self.arrivals) >= case.expected_sources, case.deadline)

        store = self._ingest(trace)
        # `fields` is the field map itself, not a JSON Schema object wrapping
        # one, so the columns are its keys.
        counts: dict[str, int] = dict.fromkeys(SPEC["entities"][OUTPUT]["fields"], 0)
        walked = await self._walk(trace, store, counts, case)

        return Row(
            shipment_no=case.shipment,
            steps=walked,
            trace=trace.dump(),
            **{key: counts.get(key, 0) for key in _COLUMNS},
        )

    def _ingest(self, trace: RunTrace) -> EntityStore:
        candidates = tuple(
            Candidate(entity=k, identified_by=e["identified_by"], fields=e.get("fields", {}))
            for k, e in SPEC["entities"].items()
            if e.get("identified_by")
        )
        payloads: dict[str, list[dict[str, Any]]] = {}
        for arrival in self.arrivals:
            for instance in json.loads(arrival.payload):
                payloads.setdefault(instance["_entity"], []).append(
                    {k: v for k, v in instance.items() if k != "_entity"}
                )

        with trace.step("extract") as step:
            store = ingest(
                [Source(ref=a.ref, name=a.name) for a in self.arrivals],
                candidates,
                Pipeline(
                    Arrived({a.ref: a.text for a in self.arrivals}),
                    classify,
                    make_extractor(payloads),
                ),
            )
            step.produced({"counts": store.counts(), "declined": len(store.skipped)})

        # A skip is never silent. "Found no certificates" and "skipped the
        # certificate" are the same empty result with different fixes.
        for skipped in store.skipped:
            trace.decline(skipped.source, skipped.reason)
        return store

    async def _walk(
        self, trace: RunTrace, store: EntityStore, counts: dict[str, int], case: Input
    ) -> list[str]:
        routes = Routes.from_edges(
            [
                (e["key"], e["from_key"], e["to_key"], tuple(e.get("on_outcomes", ())))
                for e in SPEC["edges"]
            ]
        )
        step_key, outcome, walked = "prealert_received", "arrived", []

        while step_key:
            walked.append(step_key)
            card = SPEC["primitives"][step_key]
            if card["primitive_type"] == "check":
                with trace.step(step_key) as recorder:
                    result = _CHECKS[step_key](card["config"], store, counts)
                    recorder.produced(result)
                outcome = result.outcome
            elif card.get("capabilities"):
                with trace.step(step_key) as recorder:
                    # The idempotency key is derived from the shipment and the
                    # step, so a corrected certificate re-running this check does
                    # not send the supervisor a second identical email.
                    await workflow.execute_activity_method(
                        Capabilities.invoke,
                        CapabilityCall(
                            capability=card["capabilities"][0],
                            args={"shipment": case.shipment},
                            idempotency_key=f"{case.shipment}:{step_key}",
                        ),
                        start_to_close_timeout=timedelta(seconds=10),
                    )
                    recorder.tool_called(card["capabilities"][0], {}, shadowed=True)
                if card["config"].get("is_terminal"):
                    break
            elif card["config"].get("is_terminal"):
                break
            step_key = routes.next(step_key, outcome)
        return walked


_CHECKS = {"coas_valid": coas_valid, "invoice_complete": invoice_complete}
_COLUMNS = (
    "invoices_successful",
    "invoices_failed",
    "invoices_total",
    "goods_failed",
    "failed_coa",
    "coa_success",
    "coa_total",
)


# --- the entry point the platform calls ---------------------------------------


async def run_case(case: dict[str, Any]) -> CaseOutcome:
    """Run one eval case to completion and report what it produced.

    Everything Temporal lives here, where this process's own vocabulary is in
    scope. `start_time_skipping` runs its own server, so the spec's PT48H
    deadline passes in milliseconds and no server has to be up to sweep.
    """
    arrivals = [
        Arrival(
            ref=a["ref"],
            name=a["name"],
            text=a["text"],
            payload=json.dumps(a.get("payload", [])),
        )
        for a in case["arrivals"]
    ]
    capabilities = Capabilities(
        Tools(
            {"email.send": Tool("recording", "SEND"), "system.write": Tool("recording", "WRITE")},
            {"recording": RecordingProvider()},
        ),
        mode="shadow",
    )

    queue = f"sweep-{case['key']}"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[InboundPreAlert],
            activities=[capabilities.invoke],
        ):
            handle = await env.client.start_workflow(
                InboundPreAlert.run,
                Input(shipment=case["key"], expected_sources=len(arrivals)),
                id=f"prealert-{case['key']}",
                task_queue=queue,
            )
            for arrival in arrivals:
                await handle.signal(InboundPreAlert.documents_arrived, arrival)
            # An exception in workflow code is a workflow task failure, which
            # Temporal retries forever. Without this a bug presents as silence.
            row = await asyncio.wait_for(handle.result(), timeout=120)

    return CaseOutcome.from_dump(
        {column: getattr(row, column) for column in _COLUMNS} | {"status": row.status},
        row.trace,
    )
