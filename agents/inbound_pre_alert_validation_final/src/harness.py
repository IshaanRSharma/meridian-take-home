"""The entry point `meridian eval sweep` runs: one eval case, start to finish.

The sweep is generic and this agent is not, so the split is that the agent runs
itself. Everything Temporal — starting an environment, registering the worker,
sending the signals — happens here, where this process's own vocabulary is in
scope. What goes back is a `CaseOutcome`: the row, and the trace.

**The mailbox is live.** Nothing is snapshotted, because the thing being tested
is a generated workflow actually reaching Gmail, classifying real attachments
and reading real PDFs. The inbox is fetched once per process and shared across
cases — the messages do not change between them, and refetching would be nine
round trips to read the same fifteen emails.

**Time is skipped, not waited.** `start_time_skipping` runs a real Temporal
server whose clock jumps to the next timer, so a deadline measured in days
resolves in milliseconds and the suite stays fast enough to run after every
patch.

**Every capability records rather than performs.** The graded artefact is the
row this process returns, so scoring a case needs no I/O at all — and a recorded
call is better evidence than a delivered one, because it is assertable where a
sent email is a screenshot. One flag swaps the recorder for the live provider,
which is what keeps the demo path and the eval path the same code.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from typing import Any

import mail
import spec
from ingestion import Arrival, Attachment, Ingestion
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from workflow import InboundPreAlertValidationFinal, Input, Result

from meridian.runtime.harness import CaseOutcome
from meridian.runtime.temporal.activities import Capabilities
from meridian.runtime.tools.dispatch import Tool, Tools
from meridian.runtime.tools.providers import RecordingProvider

TASK_QUEUE = "inbound-pre-alert-final"

CASE_TIMEOUT_SECONDS = 420.0
"""How long one case may take before it is called a hang.

Every layer has to be strictly tighter than the one outside it, or the inner
bound is dead code and a slow case is reported as a hang by whichever outer
timer fires first. Innermost out: the ingestion activity at 8 minutes is the
one exception — it is the work — then this, then `meridian eval sweep
--timeout`, which must exceed it.
"""

_INBOX: dict[str, tuple[mail.Message, ...]] = {}


def gmail() -> mail.Gmail:
    """The connected mailbox for this deployment."""
    return mail.Gmail(
        api_key=os.environ["COMPOSIO_API_KEY"], user_id=os.environ["COMPOSIO_ENTITY_ID"]
    )


def model_client() -> Any:
    """The model this build classifies and extracts with."""
    from openai import OpenAI  # noqa: PLC0415

    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def inbox(box: mail.Gmail) -> tuple[mail.Message, ...]:
    """Every message, fetched once per process."""
    if "messages" not in _INBOX:
        _INBOX["messages"] = box.inbox()
    return _INBOX["messages"]


def arrivals_for(shipment: str, messages: tuple[mail.Message, ...]) -> list[Arrival]:
    """The pre-alerts belonging to one shipment, as signals.

    A shipment is several emails, so this is where many messages become one
    workflow — over the same grouping function the production trigger uses,
    which is what keeps the eval measuring the real correlation rule rather than
    a copy of it that can drift.
    """
    grouped = mail.group_by_shipment(messages)
    return [
        Arrival(
            message_id=message.message_id,
            subject=message.subject,
            sender=message.sender,
            received_at=message.received_at,
            attachments=[
                Attachment(
                    message_id=a.message_id,
                    attachment_id=a.attachment_id,
                    filename=a.filename,
                    media_type=a.media_type,
                )
                for a in message.attachments
            ],
        )
        for message in grouped.get(shipment, ())
    ]


def capabilities() -> Capabilities:
    """Outward calls recorded, never made.

    Every capability this spec declares resolves to a recorder, read off
    `spec.capabilities` rather than listed here — so a card growing a new
    capability needs no change in this file.
    """
    registry = {key: Tool("recording", key.upper()) for key in spec.spec()["capabilities"]}
    return Capabilities(Tools(registry, {"recording": RecordingProvider()}), mode="shadow")


def recipients() -> dict[str, list[str]]:
    """Roles resolved to addresses, per card that names one.

    Read from the environment rather than the spec: an identity in a checksummed
    contract would make a personnel change force a new spec version.
    """
    resolved: dict[str, list[str]] = {}
    for key, entry in spec.spec()["primitives"].items():
        roles = [r["role"] for r in entry["config"].get("recipients", ()) if r.get("role")]
        if roles:
            resolved[key] = [
                os.environ.get(f"ROLE_{role.upper()}", f"{role}@example.invalid")
                for role in roles
            ]
    return resolved


async def run_case(case: Mapping[str, Any]) -> CaseOutcome:
    """Run one shipment through the real workflow and report what it produced."""
    shipment = str(case["shipment_no"])
    box = gmail()
    arrivals = arrivals_for(shipment, inbox(box))

    ingestion = Ingestion(box, model_client())
    caps = capabilities()

    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[InboundPreAlertValidationFinal],
            activities=[ingestion.read_documents, caps.invoke],
        ),
    ):
        result = await _drive(env.client, shipment, arrivals)

    return CaseOutcome.from_dump(json.loads(result.row), result.trace)


async def _drive(client: Client, shipment: str, arrivals: list[Arrival]) -> Result:
    """Start the instance, hand it its documents, wait for the row.

    Signal-with-start, exactly as production does it: the first arrival creates
    the instance and every later one reaches the instance already waiting. The
    timeout is what turns an exception inside workflow code — which Temporal
    retries forever, with no traceback — into a case that fails and says so.
    """
    handle = await client.start_workflow(
        InboundPreAlertValidationFinal.run,
        Input(shipment=shipment, recipients=recipients()),
        id=f"{spec.spec()['slug']}-{shipment}",
        task_queue=TASK_QUEUE,
    )
    for arrival in arrivals:
        await handle.signal(InboundPreAlertValidationFinal.documents_arrived, arrival)
    return await asyncio.wait_for(handle.result(), timeout=CASE_TIMEOUT_SECONDS)
