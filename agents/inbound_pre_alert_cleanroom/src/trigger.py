"""Process the mail that has arrived, against no expected answer.

The eval suite asks *was this right*. This asks *what does it say*, about a
shipment nobody has scored — which is the only question production ever gets.

**There is no second implementation of the process here.** ``poll`` surveys the
mailbox and then calls ``run_case``, the same function the sweep calls, once per
shipment. A trigger that drove the workflow itself would be a parallel path that
looks identical on the day it is written and drifts the first time either side
is patched — and the drift would be invisible, because only one of the two is
ever measured.

**With no expected row, nothing here scores.** What it can do is say whether the
run is worth believing, which is a different claim and a weaker one: did a check
run at all, did the checks look at anything, does their arithmetic hold, and
what was skipped on the way. Those are properties of the *run*, answerable
without knowing the right answer.

A check is recognised by **carrying a total**, never by its name. A board that
grows a third check needs no edit here, and one that renames a card does not
quietly stop being checked.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import spec
from events.pre_alert_documentation_arrives import KEY, Unkeyed, survey
from ingestion.recognition import openai_model
from wiring import tools

from meridian.runtime import CaseOutcome


@dataclass(frozen=True)
class Gate:
    """One thing that can be said about a run without knowing the answer.

    ``counts`` is separate from ``held`` because they are different questions.
    A declined attachment makes *what arrived* unreliable; it does not make the
    arithmetic wrong, and failing a correct row because a signature image was
    skipped would cry wolf on every message in this corpus. So it is reported
    and does not vote.
    """

    name: str
    held: bool
    counts: bool
    says: str


@dataclass(frozen=True)
class Processed:
    """One shipment run to completion, with the reasons to doubt it."""

    shipment: str
    row: dict[str, Any]
    steps: tuple[dict[str, Any], ...]
    declined: tuple[dict[str, Any], ...]
    gates: tuple[Gate, ...]

    def trustworthy(self) -> bool:
        """Whether anything about the run itself argues against the row.

        Never a claim that the numbers are right — there is nothing here to
        check them against. It is the weaker statement that the process reached
        its checks, they examined something, and what they counted adds up.
        """
        return all(gate.held for gate in self.gates if gate.counts)

    def concerns(self) -> tuple[str, ...]:
        """What did not hold, in the words an operator would use."""
        return tuple(gate.says for gate in self.gates if not gate.held)


@dataclass(frozen=True)
class Uncorrelated:
    """A pre-alert that matched the process and named no shipment.

    The whole reason this is a type rather than a skipped iteration. A poll that
    dropped these would report a clean pass over a mailbox holding work nobody
    had looked at — which is the failure mode that looks most like success.
    """

    finding: Unkeyed
    reason: str

    def as_row(self) -> dict[str, Any]:
        """What gets stored in place of a row, since there is no row."""
        return {
            "state": "needs_correlation",
            "message_id": self.finding.message_id,
            "subject": self.finding.subject,
            "sender": self.finding.sender,
            "received_at": self.finding.received_at,
            "attachments": list(self.finding.attachments),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Polled:
    """One pass of the mailbox: what ran, what could not be keyed, what was old."""

    processed: tuple[Processed, ...] = ()
    uncorrelated: tuple[Uncorrelated, ...] = ()
    already_seen: tuple[str, ...] = ()


def why_unkeyable() -> str:
    """The reason a message named no shipment, in terms of the board.

    Built from the spec's own ``correlation_key`` rather than written out. A
    fixed sentence goes stale the moment somebody re-draws the Event to
    correlate on something else, and this text is exactly what an operator reads
    when deciding whether the message is theirs — so it has to keep telling the
    truth after the board changes.

    No fallback key is offered, and that is the point. Keying an air-freight
    pre-alert by its air waybill, by its invoice, or per message are three
    defensible answers that produce three different units of work, and every
    count downstream would be internally consistent and wrong together. Which
    one is right is the process owner's decision.
    """
    correlation = spec.config(KEY)["correlation_key"]
    where = f"{correlation['entity']}.{correlation['path']}"
    return (
        f"matched the pre-alert rule and names no value for {where}, "
        "which is how this board identifies a shipment"
    )


def gates_from(steps: Sequence[Mapping[str, Any]], declined: Sequence[object]) -> tuple[Gate, ...]:
    """What can be said about a run from its own trace.

    Args:
        steps: the trajectory, as the run recorded it.
        declined: what arrived and was not used.

    Returns:
        One gate per property, each with whether it held and whether it votes.
    """
    counted = [
        step["output"]
        for step in steps
        if isinstance(step.get("output"), Mapping) and "total" in step["output"]
    ]
    examined = [one for one in counted if int(one.get("total") or 0) > 0]
    reconciles = all(
        int(one.get("passed") or 0) + int(one.get("failed") or 0) == int(one.get("total") or 0)
        for one in counted
    )
    return (
        Gate(
            name="reached a check",
            held=bool(counted),
            counts=True,
            says="a check ran" if counted else "no check ran at all",
        ),
        Gate(
            name="examined something",
            held=bool(counted) and len(examined) == len(counted),
            counts=True,
            # Silent when no check ran, because the gate above already said so
            # and "0 check(s) examined nothing" reads as a riddle.
            says=(
                f"{len(examined)} check(s) looked at rows"
                if counted and len(examined) == len(counted)
                else f"{len(counted) - len(examined)} check(s) examined nothing"
                if counted
                else "there was no check to examine anything"
            ),
        ),
        Gate(
            name="counts reconcile",
            held=reconciles,
            counts=True,
            says=(
                "passed and failed sum to total"
                if reconciles
                else "a check's passed and failed do not sum to its total"
            ),
        ),
        Gate(
            name="everything was read",
            held=not declined,
            counts=False,
            says=(
                "every attachment was read"
                if not declined
                else f"{len(declined)} attachment(s) were not read"
            ),
        ),
    )


def _processed(shipment: str, outcome: CaseOutcome) -> Processed:
    """One run, reduced to what the platform stores.

    Steps and declines become plain dictionaries here rather than at the call
    site: the route writes them straight into `run_steps`, and a pydantic model
    reaching that insert is a failure a long way from its cause.
    """
    steps = tuple(step.model_dump(mode="json") for step in outcome.steps)
    declined = tuple(gone.model_dump(mode="json") for gone in outcome.declined)
    return Processed(
        shipment=shipment,
        row=dict(outcome.output),
        steps=steps,
        declined=declined,
        gates=gates_from(steps, declined),
    )


async def poll(seen: Collection[str] = ()) -> Polled:
    """Run the most recently arrived shipment, if it has not been run before.

    Args:
        seen: shipments the platform already holds a row for. Handed in rather
            than read from anywhere, because what counts as already processed is
            the platform's record — an agent keeping its own ledger would
            disagree with the database the first time either was restored from
            a backup.

    Returns:
        What ran, what could not be keyed, and what was skipped as already done.
    """
    from entry import run_case  # noqa: PLC0415 - importable only once the agent is on sys.path

    box = tools()
    found = await survey(box, openai_model())

    # The most recently received message, and only that one.
    #
    # A trigger fired by hand asks "what just arrived", not "reconcile the whole
    # mailbox" — and the two answers differ by about forty minutes. Processing
    # everything also meant one transient download failure discarded the entire
    # pass, since a poll of one has nothing else to lose.
    #
    # Everything already recorded is still reported as skipped, so the caller
    # can see the mailbox holds more than was run.
    already = tuple(sorted(set(found.shipments) & set(seen)))
    awaiting = [found.latest] if found.latest and found.latest not in set(seen) else []

    # Sequentially, and on purpose. Each shipment stands up its own Temporal
    # environment and reads the mailbox through a shared cache; running them at
    # once multiplies the environments and the model calls for no gain, because
    # the expensive half is already done by the survey above.
    processed = [_processed(one, await run_case({"shipment_no": one})) for one in awaiting]

    return Polled(
        processed=tuple(processed),
        uncorrelated=tuple(
            Uncorrelated(finding=one, reason=why_unkeyable()) for one in found.unkeyed
        ),
        already_seen=already,
    )
