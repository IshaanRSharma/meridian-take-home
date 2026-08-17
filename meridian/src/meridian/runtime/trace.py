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

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["ok", "failed", "skipped"]


class ToolCall(BaseModel):
    """One reach outside the process, attributed to the step that made it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: str
    args: dict[str, Any] = Field(default_factory=dict)
    shadowed: bool = False
    error: str | None = None


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

        A pydantic result is reduced to its counts rather than dumped whole: the
        bundle wants ``{outcome, total, passed, failed}``, and every failing row
        as well would bury the diagnosis it exists to surface.
        """
        if isinstance(result, BaseModel):
            self._output = result.model_dump(mode="json", exclude={"failures"})
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

    @property
    def steps(self) -> tuple[Step, ...]:
        """Every step so far, in the order it ran."""
        return tuple(self._steps)

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
