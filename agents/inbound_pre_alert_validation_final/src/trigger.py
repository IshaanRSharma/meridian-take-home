"""What to do about mail that has arrived, when nobody knows the right answer.

The eval suite and this share one execution path on purpose. `poll` groups the
mailbox with the same `group_by_shipment` the suite uses and hands each shipment
to the same `run_case`, so a shipment processed here goes through the workflow
the sweep measured rather than through a production twin that drifts from it.
The only difference is what comes back: a sweep compares the row against a
historical answer, and this one has none.

**No oracle is not no verdict.** A row nobody can score can still be checked for
the ways it is obviously not trustworthy — nothing arrived, a check examined
zero rows, the counts do not add up, half the attachments were skipped. Those
are properties of the run rather than of the answer, and they are what an
operator needs before believing a number nobody has verified.

**A message that cannot be keyed is reported, never dropped.** `shipment_of`
returns None for an air-freight pre-alert: the correlation rule reads an ISO
container code and an air waybill is not one. Such a message falls out of
`group_by_shipment` and nothing anywhere says so — the trigger would appear to
work while quietly ignoring a real shipment, which is the failure mode that
looks most like success. So it becomes a result in its own right, naming what
arrived and why it could not be keyed.

Deliberately **no fallback key is invented here.** Correlating by air waybill,
by invoice number, or per message are three defensible answers that produce
three different units of work, and the unit of work is the one thing this loop
is not allowed to decide — every count stays internally consistent and wrong
together. That is a question for the process owner.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

import harness
import mail
import spec

from meridian.runtime.harness import CaseOutcome

NEEDS_CORRELATION = "needs_correlation"
"""What a pre-alert that names no shipment is, rather than what it is not.

A state, not an error. Nothing went wrong in the agent: the message is a real
pre-alert, it was read, and the process model has no rule for keying it. Calling
it a failure would send somebody to debug code that behaved correctly.
"""


@dataclass(frozen=True)
class Uncorrelated:
    """A pre-alert that matched the subject rule and named no shipment."""

    message_id: str
    subject: str
    sender: str
    received_at: str
    attachment_names: tuple[str, ...]
    reason: str

    def as_row(self) -> dict[str, Any]:
        """The shape a run row carries when there is no workflow behind it."""
        return {
            "state": NEEDS_CORRELATION,
            "message_id": self.message_id,
            "subject": self.subject,
            "sender": self.sender,
            "received_at": self.received_at,
            "attachments": list(self.attachment_names),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Checked:
    """A processed shipment, and the ways it might not be trustworthy.

    Every field is a property of the *run*, never of the answer. Together they
    are what can honestly be said about a row with no ground truth: it got as
    far as the checks, the checks looked at something, their arithmetic holds,
    and this is what was skipped on the way.
    """

    shipment: str
    row: dict[str, Any]
    reached_a_check: bool
    examined_everything: bool
    counts_reconcile: bool
    declined: tuple[dict[str, str], ...]
    steps: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def trustworthy(self) -> bool:
        """Whether anything about the run itself argues against believing it."""
        return self.reached_a_check and self.examined_everything and self.counts_reconcile

    def concerns(self) -> tuple[str, ...]:
        """Why it should not be believed, in the words an operator would use."""
        said = []
        if not self.reached_a_check:
            said.append("no check ran — nothing reached the part that decides")
        if not self.examined_everything:
            said.append("a check examined zero rows, so it agreed with nothing")
        if not self.counts_reconcile:
            said.append("a check's passed and failed do not sum to its total")
        if self.declined:
            said.append(f"{len(self.declined)} attachment(s) were not read")
        return tuple(said)


@dataclass(frozen=True)
class Polled:
    """One sweep of the mailbox: what ran, what could not be keyed, what was old."""

    processed: tuple[Checked, ...] = ()
    uncorrelated: tuple[Uncorrelated, ...] = ()
    already_seen: tuple[str, ...] = ()


def why_uncorrelatable() -> str:
    """The reason a message names no shipment, in the board's own terms.

    Built from `correlation_key` rather than written out, so the sentence names
    whatever this board actually keys on. A fixed sentence would go quietly
    stale the first time somebody re-drew the event to correlate on something
    else, and this text is what an operator reads when deciding whether the
    message is theirs to handle.
    """
    key = spec.config("pre_alert_documentation_arrives")["correlation_key"]
    return (
        "matched the pre-alert subject rule and names no container number; the "
        f"correlation key on this board is {key['entity']}.{key['path']}, an ISO "
        "container code, and an air waybill is not one"
    )


def uncorrelated_in(messages: Collection[mail.Message]) -> tuple[Uncorrelated, ...]:
    """Pre-alerts that named no shipment.

    Pure, and separated from the running so it can be asserted on without a
    mailbox or a workflow. The same predicate `group_by_shipment` uses to
    *exclude* a message, used here to *report* it — one function deciding both
    keeps the two answers from disagreeing as the recognition rule changes.
    """
    reason = why_uncorrelatable()
    return tuple(
        Uncorrelated(
            message_id=message.message_id,
            subject=message.subject,
            sender=message.sender,
            received_at=message.received_at,
            attachment_names=message.attachment_names(),
            reason=reason,
        )
        for message in messages
        if mail.matches(message) and mail.shipment_of(message) is None
    )


def inspect(shipment: str, outcome: CaseOutcome) -> Checked:
    """What can be said about a row nobody can score.

    Checks are recognised by their trace entry carrying a `total`, rather than by
    a list of names. A trigger that knew which primitives were checks would need
    editing every time the board grew one, and the board is the thing that is
    supposed to be free to change.
    """
    counted = [
        dict(step.output or {}) for step in outcome.steps if "total" in (step.output or {})
    ]
    return Checked(
        shipment=shipment,
        row=dict(outcome.output),
        reached_a_check=bool(counted),
        # Zero examined is a real state for one check on one shipment — an
        # invoice carrying no goods — but every check reporting zero is the
        # empty-run trap, where a row of zeros scores as agreement with anything
        # expecting zero. Reported at the grain that can tell them apart.
        examined_everything=bool(counted) and all(int(c.get("total", 0)) > 0 for c in counted),
        counts_reconcile=all(
            int(c.get("passed", 0)) + int(c.get("failed", 0)) == int(c.get("total", 0))
            for c in counted
        ),
        declined=tuple({"source": gone.source, "reason": gone.reason} for gone in outcome.declined),
        steps=tuple(step.model_dump(mode="json") for step in outcome.steps),
    )


async def poll(seen: Collection[str] = ()) -> Polled:
    """Run every shipment in the mailbox that has not been run before.

    `seen` is passed in rather than read from anywhere: what counts as already
    processed is the platform's record, not the agent's, and an agent keeping its
    own ledger would disagree with the database the first time one of them was
    restored from a backup.
    """
    messages = harness.inbox(harness.gmail())
    grouped = mail.group_by_shipment(messages)

    processed = []
    for shipment in sorted(grouped):
        if shipment in seen:
            continue
        # The eval path, verbatim. A production route that assembled its own
        # arrivals and drove its own workflow would be a second implementation
        # of the thing the suite measures, and the two would agree until they
        # did not.
        processed.append(inspect(shipment, await harness.run_case({"shipment_no": shipment})))

    return Polled(
        processed=tuple(processed),
        uncorrelated=uncorrelated_in(messages),
        already_seen=tuple(sorted(set(grouped) & set(seen))),
    )
