"""What a generated agent hands back for one eval case.

The sweep has to run *any* agent, and every interesting part of running one is
specific to the process it came from: which signals it takes, how many documents
it waits for, what its input looks like, which providers it wires up. A harness
that knew those things would know one agent.

So the split is the other way round. **The agent runs itself and returns this;
the harness loads it, compares it and stores it.** ``build.json`` names an entry
point module exposing one coroutine::

    async def run_case(case: Mapping[str, Any]) -> CaseOutcome

``case`` is ``eval_cases.input`` verbatim. Everything Temporal — starting an
environment, registering a worker, sending signals — happens inside that
function, where the agent's own vocabulary is in scope.

``CaseOutcome`` is built *outside* the workflow, so it never crosses Temporal's
payload converter. How the trace leaves the sandbox is the agent's business;
``RunTrace.dump()`` and ``from_dump`` make the obvious way a single line at each
end, because a JSON string crosses the boundary where a model would not.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from meridian.runtime.trace import Declined, Step


class CaseOutcome(BaseModel):
    """One eval case, as it actually ran.

    ``output`` is the row the process produces, compared column by column
    against ``eval_cases.expected_output``. It is deliberately untyped: the
    columns belong to the customer's process, not to this module, and a schema
    here would have to be edited every time a board grows a field.

    Everything else is the trace. It is optional because a build that runs the
    cases and records nothing still scores — that is the first point on the
    curve, and refusing it would lose it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    output: dict[str, Any] = Field(default_factory=dict)
    steps: tuple[Step, ...] = ()
    declined: tuple[Declined, ...] = ()
    spec_version: int = 0
    spec_checksum: str = ""

    @classmethod
    def from_dump(cls, output: Mapping[str, Any], dump: str) -> CaseOutcome:
        """Rebuild from an eval row and a ``RunTrace.dump()``.

        Validation is the point. Rule 4 asks a generated agent to keep the
        trace's shape, and a malformed step caught here names the field that is
        wrong — where the same step reaching ``run_steps`` would be a row
        nothing could localise from, discovered much later by a person reading a
        bundle with a hole in it.
        """
        return cls.model_validate(dict(json.loads(dump)) | {"output": dict(output)})


@runtime_checkable
class Polled(Protocol):
    """What one pass over the mailbox produced.

    Structural rather than a base class: a generated agent names its own types
    for what it ran and what it could not key, and inheriting from ours would
    make the scaffold a framework instead of a scaffold.
    """

    @property
    def processed(self) -> tuple[Any, ...]:
        """Shipments this pass ran to completion."""
        ...

    @property
    def uncorrelated(self) -> tuple[Any, ...]:
        """Messages that matched the process and named no shipment.

        Reported rather than dropped. A pre-alert nobody can key is a gap in the
        process model, and a poll that silently skipped it would report a clean
        pass over a mailbox holding unexamined work.
        """
        ...

    @property
    def already_seen(self) -> tuple[str, ...]:
        """Shipments skipped because the platform already has a row for them."""
        ...


@runtime_checkable
class TriggerPoll(Protocol):
    """The second thing a generated agent exposes, beside ``run_case``.

    Declared here for the reason ``RunCase`` is: it was not, and two authors
    wrote the same seam independently — one taking ``(client, messages,
    task_queue)`` and returning handles, the other taking ``seen`` and running
    each shipment to completion. Neither was wrong; they simply never agreed,
    and the mismatch surfaced as a background task that answered 202 and did
    nothing. An entry point's shape is a contract, not a preference.

    **The agent owns the whole pass** — fetching the mailbox, running each
    shipment, reporting what happened. ``seen`` is handed in rather than read,
    because what counts as already processed is the platform's record; an agent
    keeping its own ledger would disagree with the database the first time
    either was restored from a backup.
    """

    def __call__(self, seen: Collection[str] = ()) -> Any:
        """Run every shipment in the mailbox that is not in ``seen``."""
        ...
