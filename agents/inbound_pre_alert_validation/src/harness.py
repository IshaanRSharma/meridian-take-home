"""The entry point `meridian eval sweep` runs: one eval case, start to finish.

The sweep is generic and this agent is not, so the split is that the agent runs
itself. Everything Temporal — starting an environment, registering the worker,
sending the signals — happens here, where this process's own vocabulary is in
scope. What goes back is a `CaseOutcome`: the row, and the trace.

**The mailbox is live.** Nothing is snapshotted, because the thing being tested
is a generated workflow actually reaching Gmail, classifying real attachments
and reading real PDFs. The inbox is fetched once per process and shared across
cases — the messages do not change between them, and refetching would be nine
round trips to read the same fourteen emails.

**Time is skipped, not waited.** `start_time_skipping` runs a real Temporal
server whose clock jumps to the next timer, so a deadline measured in days
resolves in milliseconds and the suite stays fast enough to run after every
patch.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Any

import mail
import spec
from activities import Arrival, Attachment, Ingestion
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from workflow import InboundPreAlertValidation, Input, Result

from meridian.runtime.harness import CaseOutcome
from meridian.runtime.temporal.activities import Capabilities
from meridian.runtime.tools.dispatch import Tool, Tools
from meridian.runtime.tools.providers import RecordingProvider

TASK_QUEUE = "inbound-pre-alert"
CASE_TIMEOUT_SECONDS = 300.0
"""How long one case may take before it is called a hang.

Every layer has to be strictly tighter than the one outside it, or the inner
bound is dead code and a slow case is reported as a hang by whichever outer
timer fires first. Innermost out: the ingestion activity, then this, then
`meridian eval sweep --timeout`, which must exceed it."""

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
    workflow — the same grouping the production trigger does, over the same
    function, which is what keeps the eval measuring the real correlation rule.
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

    Every capability this spec declares resolves to a recorder. The graded
    artefact is the row the process produces, not a delivered email, and a
    recorded call is the better evidence anyway: it is assertable, where a sent
    message is a screenshot.
    """
    registry = {key: Tool("recording", key.upper()) for key in spec.spec()["capabilities"]}
    return Capabilities(Tools(registry, {"recording": RecordingProvider()}), mode="shadow")


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
            workflows=[InboundPreAlertValidation],
            activities=[ingestion.read_documents, caps.invoke],
        ),
    ):
        result = await _drive(env.client, shipment, arrivals)

    import json  # noqa: PLC0415 - only needed to unwrap the workflow's return

    return CaseOutcome.from_dump(json.loads(result.row), result.trace)


async def _drive(client: Client, shipment: str, arrivals: list[Arrival]) -> Result:
    """Start the instance, hand it its documents, wait for the row.

    Signal-with-start, exactly as production does it: the first arrival creates
    the instance and every later one reaches the instance already waiting. The
    timeout is what turns an exception inside workflow code — which Temporal
    retries forever, with no traceback — into a case that fails and says so.
    """
    handle = await client.start_workflow(
        InboundPreAlertValidation.run,
        Input(shipment=shipment, recipients=_recipients()),
        id=f"{spec.spec()['slug']}-{shipment}",
        task_queue=TASK_QUEUE,
    )
    for arrival in arrivals:
        await handle.signal(InboundPreAlertValidation.documents_arrived, arrival)
    return await asyncio.wait_for(handle.result(), timeout=CASE_TIMEOUT_SECONDS)


def _recipients() -> dict[str, list[str]]:
    """Roles resolved to addresses, per card that names one.

    Read from the environment rather than the spec: an identity in a checksummed
    contract would make a personnel change force a new spec version.
    """
    resolved: dict[str, list[str]] = {}
    for key, entry in spec.spec()["primitives"].items():
        roles = [r["role"] for r in entry["config"].get("recipients", ()) if r.get("role")]
        if roles:
            resolved[key] = [
                os.environ.get(f"ROLE_{r.upper()}", f"{r}@example.invalid") for r in roles
            ]
    return resolved
