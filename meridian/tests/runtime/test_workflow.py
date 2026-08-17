"""A real Temporal workflow, running against a real (test) server.

Everything else in this suite substitutes the SDK. This does not: it starts a
time-skipping environment, registers a worker, and executes a workflow that
imports the runtime, waits on a deadline, and calls an activity.

Three things only this can catch. Whether the runtime survives the workflow
sandbox at all — the sandbox reloads non-passed-through modules and restricts
what they may do, so a module that reads a clock at import fails here and
nowhere else. Whether the activity boundary is wired. And whether a `PT48H`
deadline is testable in milliseconds, which is the claim the whole eval design
rests on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from meridian.runtime.temporal.activities import Capabilities, CapabilityCall
from meridian.runtime.tools.dispatch import Tool, Tools
from meridian.runtime.tools.providers import RecordingProvider

# pydantic_core must be passed through explicitly. Without it the sandbox
# imports it lazily on first model construction, warns, and pays a reload.
with workflow.unsafe.imports_passed_through():
    import pydantic_core  # noqa: F401 - pydantic loads it lazily on first model use

    from meridian.runtime import CheckResult, Failure, RunTrace
    from meridian.runtime.routing import Routes
    from meridian.runtime.temporal.waits import await_inputs

TASK_QUEUE = "meridian-test"


@dataclass
class Arrival:
    """One document turning up. A single dataclass, so the signal can grow."""

    entity: str
    batch_no: str | None = None


@dataclass
class Result:
    """What the workflow returns.

    A dataclass rather than ``dict[str, object]``: Temporal's payload converter
    refuses ``object`` and fails at the boundary with a type error that names a
    key rather than a cause. Concrete types, or nothing crosses.
    """

    outcome: str
    settled: bool
    steps: list[str] = field(default_factory=list)
    next_step: str | None = None


@dataclass
class Input:
    """One case. Also a single dataclass, for the same reason."""

    shipment: str
    expected_batches: int = 2
    deadline: str | None = "PT48H"


@workflow.defn
class ToyPreAlert:
    """Wait for documents, check them, report if anything is missing.

    Deliberately the smallest thing that exercises the shape a generated agent
    has: a signal, a deadline, a pure check in workflow code, and an activity
    for the one step that touches the world.
    """

    def __init__(self) -> None:
        self.arrivals: list[Arrival] = []

    @workflow.signal
    def documents_arrived(self, arrival: Arrival) -> None:
        self.arrivals.append(arrival)

    @workflow.run
    async def run(self, case: Input) -> Result:
        trace = RunTrace(spec_version=1, spec_checksum="toy")

        settled = await await_inputs(
            lambda: len(self.arrivals) >= case.expected_batches, case.deadline
        )

        with trace.step("coas_valid") as step:
            missing = case.expected_batches - len(self.arrivals)
            result = CheckResult(
                outcome="pass" if settled else "missing_coa",
                total=case.expected_batches,
                passed=len(self.arrivals),
                failed=missing,
                # A NESTED MODEL. Every real check reports these; the toy
                # omitted them, which is exactly why this went unnoticed.
                failures=tuple(
                    Failure(grain="per_line_item", locator=f"x[{i}]", reason="absent")
                    for i in range(missing)
                ),
            )
            step.produced(result)

        routes = Routes.from_edges(
            [
                ("e1", "coas_valid", "report", ("missing_coa",)),
                ("e2", "coas_valid", "done", ("pass",)),
            ]
        )
        following = routes.next("coas_valid", result.outcome)

        if following == "report":
            await workflow.execute_activity_method(
                Capabilities.invoke,
                CapabilityCall(capability="email.send", args={"shipment": case.shipment}),
                start_to_close_timeout=timedelta(seconds=10),
            )

        return Result(
            outcome=result.outcome,
            settled=settled,
            next_step=following,
            steps=[s.name for s in trace.steps],
        )


@pytest.fixture
def capabilities() -> Capabilities:
    provider = RecordingProvider()
    tools = Tools(
        registry={"email.send": Tool(provider="recording", action="SEND")},
        providers={"recording": provider},
    )
    return Capabilities(tools, mode="live")


async def _run(env: WorkflowEnvironment, caps: Capabilities, case: Input, arrivals: int) -> Result:
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[ToyPreAlert],
        activities=[caps.invoke],
    ):
        handle = await env.client.start_workflow(
            ToyPreAlert.run, case, id=f"toy-{case.shipment}", task_queue=TASK_QUEUE
        )
        for index in range(arrivals):
            await handle.signal(ToyPreAlert.documents_arrived, Arrival("coa", f"B{index}"))
        return await handle.result()


async def test_the_runtime_survives_the_workflow_sandbox(capabilities: Capabilities):
    # The one thing no other test can check. The sandbox reloads and restricts
    # modules; anything reading a clock or doing I/O at import fails here.
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run(env, capabilities, Input(shipment="CAAU4056270"), arrivals=2)

    assert result.outcome == "pass"
    assert result.steps == ["coas_valid"]


async def test_a_forty_eight_hour_deadline_expires_in_milliseconds(capabilities: Capabilities):
    # The claim the whole eval design rests on. Nothing signals, so the workflow
    # waits out PT48H — and the test returns immediately.
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run(env, capabilities, Input(shipment="TTNU8982561"), arrivals=0)

    assert result.settled is False
    assert result.outcome == "missing_coa"


async def test_the_deadline_branch_reaches_the_activity(capabilities: Capabilities):
    # Proves the activity boundary is wired end to end: a check in workflow code
    # routes to an outcome, and that outcome performs work outside the sandbox.
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run(env, capabilities, Input(shipment="MNBU3974949"), arrivals=1)

    assert result.next_step == "report"


async def test_no_deadline_waits_rather_than_expiring(capabilities: Capabilities):
    # `timeout=0` fires immediately, so an absent deadline must reach
    # wait_condition as None. If it did not, this would come back unsettled.
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run(
            env, capabilities, Input(shipment="176-26926281", deadline=None), arrivals=2
        )

    assert result.settled is True
