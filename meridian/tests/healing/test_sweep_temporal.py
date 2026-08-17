"""One real workflow, loaded and run the way the sweep will load and run one.

Every other test here substitutes the agent. This does not: it writes a file
that defines a `@workflow.defn`, hands the path to `agent_loaded`, and lets
the sweep start a time-skipping environment, signal it and score the result.

Three things only this can catch, and each has already cost somebody an
afternoon somewhere:

- whether an agent loaded from a **path** rather than from the import path can
  be defined as a workflow at all. Temporal's sandbox re-imports the module a
  workflow is declared in, so a module that Python cannot find by name is a
  failure that appears nowhere else.
- whether a `RunTrace` survives the crossing back out as a string, with the
  failed step still on it.
- whether a `PT48H` deadline really does pass in milliseconds, which is the
  claim the whole eval loop rests on.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from meridian.domain.build import Build, EvalCase
from meridian.healing import sweep as sweep_
from meridian.repositories import builds as builds_repo
from meridian.repositories import evals as evals_repo

from .conftest import a_spec

pytestmark = pytest.mark.db

AGENT = '''
"""A generated agent, hand-written: a signal, a deadline, a check, a return."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

with workflow.unsafe.imports_passed_through():
    import pydantic_core  # noqa: F401

    from meridian.runtime import CheckResult, Failure, RunTrace
    from meridian.runtime.harness import CaseOutcome
    from meridian.runtime.temporal.waits import await_inputs


@dataclass
class Arrival:
    batch_no: str


@dataclass
class Input:
    shipment: str
    expected: int = 2
    deadline: str | None = "PT48H"


@dataclass
class Row:
    """Concrete types only — Temporal's converter refuses `object`.

    The trace crosses as a string for the same reason: a tuple of nested models
    is not something the payload converter takes.
    """

    coa_total: int = 0
    coa_success: int = 0
    failed_coa: int = 0
    status: str = "ACTIVE"
    trace: str = "{}"


@workflow.defn
class ToyPreAlert:
    def __init__(self) -> None:
        self.arrivals: list[Arrival] = []

    @workflow.signal
    def documents_arrived(self, arrival: Arrival) -> None:
        self.arrivals.append(arrival)

    @workflow.run
    async def run(self, case: Input) -> Row:
        trace = RunTrace(spec_version=1, spec_checksum="abc123")

        settled = await await_inputs(lambda: len(self.arrivals) >= case.expected, case.deadline)

        with trace.step("coas_valid") as step:
            missing = case.expected - len(self.arrivals)
            result = CheckResult(
                outcome="pass" if not missing else "missing_coa",
                total=case.expected,
                passed=len(self.arrivals),
                failed=missing,
                failures=tuple(
                    Failure(grain="per_line_item", locator=f"batch[{i}]", reason="no certificate")
                    for i in range(missing)
                ),
            )
            step.produced(result)
        if not settled:
            trace.decline("the deadline", "expired before every certificate arrived")

        return Row(
            coa_total=result.total,
            coa_success=result.passed,
            failed_coa=result.failed,
            trace=trace.dump(),
        )


async def run_case(case):
    """Start a workflow, signal it, and hand back what it produced."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        queue = f"sweep-{case['shipment']}"
        async with Worker(env.client, task_queue=queue, workflows=[ToyPreAlert]):
            handle = await env.client.start_workflow(
                ToyPreAlert.run,
                Input(shipment=case["shipment"], expected=case["expected"]),
                id=f"sweep-{case['shipment']}",
                task_queue=queue,
            )
            for batch in case["arrivals"]:
                await handle.signal(ToyPreAlert.documents_arrived, Arrival(batch_no=batch))
            # An exception in workflow code is a workflow task failure, which
            # Temporal retries forever. Without this a bug presents as silence.
            row = await asyncio.wait_for(handle.result(), timeout=60)

    return CaseOutcome.from_dump(
        {"coa_total": row.coa_total, "coa_success": row.coa_success,
         "failed_coa": row.failed_coa, "status": row.status},
        row.trace,
    )
'''


@pytest.fixture
def temporal_agent(tmp_path: Path) -> Path:
    where = tmp_path / "agents" / "toy_prealert"
    where.mkdir(parents=True)
    (where / "agent.py").write_text(AGENT)
    (where / "build.json").write_text(json.dumps({"entry_point": "agent.py", "file_map": {}}))
    return where


@pytest.fixture
async def temporal_build(connection: asyncpg.Connection, spec_id, temporal_agent: Path) -> Build:
    return await builds_repo.save(
        connection,
        Build(
            spec_id=spec_id,
            source_ref="agents/toy_prealert@a3f9c21",
            file_map={"coas_valid": "agent.py"},
            entry_point="agent.py",
        ),
    )


@pytest.fixture
async def temporal_cases(connection: asyncpg.Connection, spec_id) -> tuple[EvalCase, ...]:
    return (
        await evals_repo.save_case(
            connection,
            spec_id,
            EvalCase(
                key="CAAU4056270",
                input={"shipment": "CAAU4056270", "expected": 2, "arrivals": ["B1", "B2"]},
                expected_output={"coa_total": 2, "coa_success": 2, "failed_coa": 0},
            ),
        ),
        await evals_repo.save_case(
            connection,
            spec_id,
            EvalCase(
                key="TTNU8982561",
                input={"shipment": "TTNU8982561", "expected": 2, "arrivals": ["B1"]},
                expected_output={"coa_total": 2, "coa_success": 2, "failed_coa": 0},
            ),
        ),
    )


async def test_a_real_workflow_loaded_from_a_path_runs_and_scores(
    connection: asyncpg.Connection, temporal_build: Build, temporal_cases, temporal_agent: Path
):
    swept = await sweep_.sweep(
        connection,
        build=temporal_build,
        spec=a_spec(),
        cases=temporal_cases,
        agents_root=temporal_agent.parent,
        cycle_id=uuid4(),
    )

    assert swept.errored() == ()
    # The second case is one certificate short: two of three columns disagree.
    assert (swept.passed, swept.total) == (4, 6)
    assert not swept.empty


async def test_the_trace_crosses_the_workflow_boundary_and_lands_in_run_steps(
    connection: asyncpg.Connection, temporal_build: Build, temporal_cases, temporal_agent: Path
):
    await sweep_.sweep(
        connection,
        build=temporal_build,
        spec=a_spec(),
        cases=temporal_cases,
        agents_root=temporal_agent.parent,
        cycle_id=uuid4(),
    )

    rows = await connection.fetch(
        "select s.primitive_key, s.output from run_steps s join runs r on r.id = s.run_id "
        "join eval_cases c on c.id = r.case_id "
        "where r.build_id = $1 and c.key = 'TTNU8982561'",
        temporal_build.identity,
    )
    (row,) = rows
    assert row["primitive_key"] == "coas_valid"
    assert json.loads(row["output"])["outcome"] == "missing_coa"


async def test_a_forty_eight_hour_deadline_still_expires_in_milliseconds(
    connection: asyncpg.Connection, temporal_build: Build, spec_id, temporal_agent: Path
):
    # Nothing signals, so the workflow waits out PT48H. If time skipping did not
    # survive being started from inside a loaded entry point, this would sit for
    # two days rather than failing.
    waiting = await evals_repo.save_case(
        connection,
        spec_id,
        EvalCase(
            key="MNBU3974949",
            input={"shipment": "MNBU3974949", "expected": 2, "arrivals": []},
            expected_output={"coa_total": 2, "coa_success": 0, "failed_coa": 2},
        ),
    )

    swept = await sweep_.sweep(
        connection,
        build=temporal_build,
        spec=a_spec(),
        cases=[waiting],
        agents_root=temporal_agent.parent,
        cycle_id=uuid4(),
        case_timeout=90,
    )

    assert swept.errored() == ()
    assert swept.results[0].passed()
