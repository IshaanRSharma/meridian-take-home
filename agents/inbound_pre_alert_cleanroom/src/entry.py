"""The entry point: one eval case, run end to end, as a ``CaseOutcome``.

The sweep is generic and this agent is not, so the split is the other way round
from the usual harness: **the agent runs itself and returns this; the platform
loads it, compares it and stores it.** Everything Temporal — starting an
environment, registering a worker, sending signals — happens in here, where this
process's own vocabulary is in scope.

Three things about the shape, each of which bites if assumed otherwise:

* **Time is skipped, not waited.** ``start_time_skipping`` runs a real server
  whose clock jumps to the next timer, so the settle window resolves in
  milliseconds and the suite stays fast enough to run after every patch. Auto
  skipping is held off while the signals are delivered, because a clock that
  jumps ahead of the paperwork would settle a shipment before its documents
  arrived.
* **The result is wrapped in a timeout.** An exception in workflow code is a
  workflow task failure, retried forever with no traceback — so without one a
  broken agent does not fail, it hangs, and the sweep waits with it.
* **The outcome is built out here**, never inside the workflow, so it never
  crosses Temporal's payload converter.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import spec
from events.pre_alert_documentation_arrives import gather
from ingestion.recognition import openai_model
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from wiring import roles as role_table
from wiring import tools
from workflow import InboundPreAlertValidation, RunRequest, RunResult

from meridian.runtime import CaseOutcome
from meridian.runtime.temporal.activities import Capabilities

TASK_QUEUE = "inbound-pre-alert-cleanroom"

# How long the caller holds the instance open once the process has stopped
# making progress. Not a deadline from the spec — the Event declares none, and
# substituting one would turn "no time limit" into "expire at once". It is the
# suite's own stopping rule: the corpus is finite, so nothing more is coming.
# Under time skipping this costs no wall-clock time at all.
SETTLE = "PT30M"

# The sweep gives a case three minutes. This gives up first, so the failure that
# comes back names the workflow rather than the harness.
RESULT_TIMEOUT = 150.0


def _shipment(case: Mapping[str, Any]) -> str:
    """The container this case is about, as the eval set states it."""
    try:
        return str(case["shipment_no"])
    except KeyError:
        wanted = "{'shipment_no': '<container>'}"
        raise KeyError(f"an eval case for this agent is {wanted}; got {dict(case)}") from None


async def run_case(case: Mapping[str, Any]) -> CaseOutcome:
    """Run one shipment through the whole board and report what it produced.

    Args:
        case: ``eval_cases.input`` verbatim — ``{"shipment_no": "<container>"}``.

    Returns:
        The row the process produced, with the trajectory that produced it.
    """
    shipment_no = _shipment(case)
    box = tools(live_send=os.environ.get("MERIDIAN_LIVE_SEND") == "1")

    # Reading the paperwork happens before any workflow exists, because the
    # correlation key is a field on an extracted document: nothing can say which
    # shipment an email belongs to until its invoice has been read.
    gathered = await gather(box, openai_model(), shipment_no)

    request = RunRequest(
        shipment_no=shipment_no,
        roles=role_table(*_role_names()),
        settle=SETTLE,
        matched=gathered.matched,
        uncorrelated=list(gathered.uncorrelated),
    )
    result = await _execute(box, request, gathered.arrivals)
    return CaseOutcome.from_dump(result.summary, result.trace)


async def _execute(box: Any, request: RunRequest, arrivals: Any) -> RunResult:
    """Start the workflow, deliver every arrival, and wait for the row."""
    capabilities = Capabilities(box, mode="record")
    async with await WorkflowEnvironment.start_time_skipping() as env, Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[InboundPreAlertValidation],
        activities=[capabilities.invoke],
    ):
        with env.auto_time_skipping_disabled():
            handle = await env.client.start_workflow(
                InboundPreAlertValidation.run,
                request,
                # `signal_with_start` in production, where a corrected
                # certificate on Thursday has to reach the instance started
                # on Tuesday. Here every arrival is already in hand, and a
                # fresh id per run keeps two sweeps from colliding.
                id=f"{spec.SPEC['slug']}-{request.shipment_no}-{uuid4().hex[:8]}",
                task_queue=TASK_QUEUE,
            )
            for arrival in arrivals:
                await handle.signal(
                    InboundPreAlertValidation.documents_arrived, arrival
                )
        answer: RunResult = await asyncio.wait_for(handle.result(), timeout=RESULT_TIMEOUT)
        return answer


def _role_names() -> tuple[str, ...]:
    """Every role any Action names, so bindings are resolved once per run."""
    return tuple(
        sorted(
            {
                str(who["role"])
                for key in spec.keys_of("action")
                for who in spec.config(key).get("recipients") or []
                if who.get("kind") == "role"
            }
        )
    )
