"""The trajectory a failure bundle is assembled from.

The bundle is the product: an engineer pastes it into a coding agent and the
diagnosis has to be inside the paste. So the property that matters most here is
that a step which *failed* is still recorded — a trace of successes omits
exactly the line somebody needed to read.

**No durations are measured in workflow code.** Reading a clock to time a step
is precisely the nondeterminism Temporal replays into a bug, and a pure Check
re-runs instantly on replay anyway, so the number would be a fiction.
``latency_ms`` is filled in for activities by the worker interceptor, which runs
outside the sandbox, and stays ``None`` for anything in-workflow.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["ok", "failed", "skipped"]

# How many failing rows a step carries into the bundle. Enough to show a
# pattern — whitespace, case, a prefix — and few enough that the reader still
# sees the trace underneath them.
EVIDENCE_LIMIT = 5


def _failing(failures: object) -> dict[str, Any]:
    """A sample of what did not pass, as the values a person would name.

    ``subject`` is the one field every operator fills with the thing the failure
    is *about*, which is why it can be asked for generically here.
    """
    if not isinstance(failures, tuple | list) or not failures:
        return {}
    return {
        "failing": [
            {"subject": getattr(f, "subject", ""), "reason": getattr(f, "reason", "")}
            | ({"detail": dict(f.detail)} if getattr(f, "detail", None) else {})
            for f in failures[:EVIDENCE_LIMIT]
        ]
    }


class ToolCall(BaseModel):
    """One reach outside the process, attributed to the step that made it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: str
    args: dict[str, Any] = Field(default_factory=dict)
    shadowed: bool = False
    error: str | None = None


class Declined(BaseModel):
    """Something that arrived and was not used, and why.

    Skipping is correct — the SOP says *locate* the invoice among the
    attachments — but it must never be silent. "Found no invoices" and "skipped
    the invoice" are the same empty result with entirely different fixes, and
    only one of them is a bad recognition rule.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    reason: str


class Step(BaseModel):
    """One named unit of work, as it happened."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int
    name: str
    attempt: int = 1
    status: Status = "ok"
    output: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: int | None = None
    tool_calls: tuple[ToolCall, ...] = ()


class StepRecorder:
    """Handed to the body of a step so it can describe what it did."""

    def __init__(self) -> None:
        """Start with nothing recorded; the body describes itself as it runs."""
        self._output: dict[str, Any] | None = None
        self._error: str | None = None
        self._status: Status | None = None
        self._tool_calls: list[ToolCall] = []

    def produced(self, result: object) -> None:
        """Record what the step returned.

        A pydantic result is reduced to its counts plus a **sample of what
        failed**. Every failing row would bury the diagnosis; none of them
        buries it just as thoroughly, because ``2 unmatched`` sends somebody to
        the source documents where ``['UAC25022 ', 'uac25019']`` shows trailing
        whitespace and case variance in one read. A few are the diagnosis, all
        of them are a data dump.
        """
        if isinstance(result, BaseModel):
            self._output = result.model_dump(mode="json", exclude={"failures"})
            self._output |= _failing(getattr(result, "failures", ()))
        elif isinstance(result, Mapping):
            self._output = dict(result)
        else:
            self._output = {"value": result}

    def failed(self, error: BaseException) -> None:
        """Record why this step did not succeed."""
        self._status = "failed"
        self._error = f"{type(error).__name__}: {error}"

    def skipped(self, reason: str) -> None:
        """Record that the step did not run, and what stood in the way."""
        self._status = "skipped"
        self._error = reason

    def tool_called(
        self,
        capability: str,
        args: Mapping[str, Any] | None = None,
        *,
        shadowed: bool = False,
        error: str | None = None,
    ) -> None:
        """Attribute one outward call to this step."""
        self._tool_calls.append(
            ToolCall(capability=capability, args=dict(args or {}), shadowed=shadowed, error=error)
        )


class RunTrace:
    """Every step of one case, in the order it ran.

    Mutable by design and instance-scoped — one trace per workflow execution,
    never module state, so the runtime stays safe to pass through the sandbox.
    """

    def __init__(self, spec_version: int, spec_checksum: str) -> None:
        """Name the spec this run was built from, so a bundle is reproducible."""
        self.spec_version = spec_version
        self.spec_checksum = spec_checksum
        self._steps: list[Step] = []
        self._declined: list[Declined] = []

    @property
    def steps(self) -> tuple[Step, ...]:
        """Every step so far, in the order it ran."""
        return tuple(self._steps)

    @property
    def declined(self) -> tuple[Declined, ...]:
        """Everything that arrived and was not used."""
        return tuple(self._declined)

    def decline(self, source: str, reason: str) -> None:
        """Record something skipped, so an empty result has a cause beside it."""
        self._declined.append(Declined(source=source, reason=reason))

    def dump(self) -> str:
        """The whole trace as one JSON string, for the trip out of the sandbox.

        Temporal's payload converter takes concrete types across the workflow
        boundary, and a tuple of nested models is not one of them. A string is,
        so the agent puts this on whatever dataclass it returns and the harness
        reads it back with ``CaseOutcome.from_dump``. One line at each end, and
        no part of the trace is flattened away in between.
        """
        return json.dumps(
            {
                "spec_version": self.spec_version,
                "spec_checksum": self.spec_checksum,
                "steps": [step.model_dump(mode="json") for step in self._steps],
                "declined": [gone.model_dump(mode="json") for gone in self._declined],
            }
        )

    def unrecognised_share(self) -> float:
        """What fraction of everything that arrived was skipped.

        A finding rather than a statistic. One signature image among seven
        attachments is ordinary; six of seven means the recognition rules are
        wrong, and that is the reviewer signal worth raising before anybody
        debugs a check that had nothing to read.
        """
        used = sum(1 for step in self._steps if step.name.startswith("extract"))
        total = used + len(self._declined)
        return len(self._declined) / total if total else 0.0

    @contextmanager
    def step(self, name: str, attempt: int = 1) -> Iterator[StepRecorder]:
        """Run a named step and record it, whether or not it succeeds.

        An exception is recorded and then re-raised: the trace is a witness, not
        an error handler, and swallowing here would hide a failure from the
        policy that is supposed to decide about it.
        """
        recorder = StepRecorder()
        try:
            yield recorder
        except BaseException as error:
            if recorder._status is None:
                recorder.failed(error)
            raise
        finally:
            self._steps.append(
                Step(
                    seq=len(self._steps) + 1,
                    name=name,
                    attempt=attempt,
                    status=recorder._status or "ok",
                    output=recorder._output,
                    error=recorder._error,
                    tool_calls=tuple(recorder._tool_calls),
                )
            )
