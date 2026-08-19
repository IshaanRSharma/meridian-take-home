"""What starts a workflow when mail actually arrives.

The event card says `timing.mode: on_arrival`, which compiles to
signal-with-start: one call that creates the instance if this shipment has never
been seen and signals the instance already waiting if it has. That single call
is why a corrected certificate arriving on Thursday reaches the run that
Tuesday's invoice began.

**The match condition is evaluated here, outside the workflow.** Deciding
whether a message is a pre-alert is I/O-adjacent and would be a replay hazard
inside workflow code; more importantly it decides *whether a workflow exists at
all*, which nothing inside one can answer.

**A message naming no container is reported, never dropped.** `shipment_of`
returns `None` for an air-freight pre-alert, which carries an air waybill and no
container — and the correlation key this board declares is a container number.
Correlating it on an air waybill instead would be choosing a different unit of
work, and every count downstream would be internally consistent and wrong
together. That is the one decision this agent is not allowed to make, so it
comes back as an `Uncorrelated` for somebody to look at rather than as silence.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import mail
import spec
from ingestion import Arrival, Attachment
from temporalio.client import Client, WorkflowHandle
from workflow import InboundPreAlertValidationFinal, Input


@dataclass(frozen=True)
class Uncorrelated:
    """A pre-alert this board cannot file, and why.

    Carried rather than logged, because the caller is what decides whether an
    unfiled shipment is a queue entry, an alert or a line in a report — and a
    silent skip is how a shipment goes unprocessed with nothing anywhere saying
    so.
    """

    message_id: str
    subject: str
    sender: str
    received_at: str
    attachments: tuple[str, ...]
    reason: str


def arrivals_of(message: mail.Message) -> Arrival:
    """One message, in the shape the workflow's signal takes."""
    return Arrival(
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


def uncorrelated_in(messages: Sequence[mail.Message]) -> list[Uncorrelated]:
    """Pre-alerts that match the event but name no container.

    The same predicate `group_by_shipment` uses to *exclude* them, applied here
    to *report* them. One function deciding both keeps the two answers from
    disagreeing as the recognition rule changes.
    """
    key = spec.config("pre_alert_documentation_arrives")["correlation_key"]
    return [
        Uncorrelated(
            message_id=message.message_id,
            subject=message.subject,
            sender=message.sender,
            received_at=message.received_at,
            attachments=message.attachment_names(),
            reason=(
                f"this board correlates on {key['entity']}.{key['path']}, an ISO container "
                "code, and this message names none — an air waybill is not one"
            ),
        )
        for message in messages
        if mail.matches(message) and mail.shipment_of(message) is None
    ]


async def poll(
    client: Client, messages: Sequence[mail.Message], task_queue: str
) -> tuple[dict[str, WorkflowHandle[InboundPreAlertValidationFinal, object]], list[Uncorrelated]]:
    """Signal every shipment in this batch, and hand back what could not be filed.

    Returns handles rather than results: a shipment whose paperwork is still
    arriving is not finished, and waiting here would block the poll on the
    slowest open shipment in the mailbox.
    """
    handles: dict[str, WorkflowHandle[InboundPreAlertValidationFinal, object]] = {}
    for shipment, group in mail.group_by_shipment(messages).items():
        for message in group:
            handles[shipment] = await client.start_workflow(
                InboundPreAlertValidationFinal.run,
                Input(shipment=shipment),
                id=f"{spec.spec()['slug']}-{shipment}",
                task_queue=task_queue,
                start_signal="documents_arrived",
                start_signal_args=[arrivals_of(message)],
            )
    return handles, uncorrelated_in(messages)
