"""Running every case and writing down what happened.

The properties worth pinning are the ones a plausible implementation gets wrong:
a case that raises must not take the sweep with it, a re-sweep must replace
rather than accumulate, and the trace must reach `run_steps` intact — including
the step that failed, which is the line the whole bundle is built around.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from meridian.domain.build import Build, EvalCase
from meridian.healing import sweep as sweep_
from meridian.repositories import builds as builds_repo
from meridian.repositories import evals as evals_repo

from .conftest import EXPECTED, a_spec

pytestmark = pytest.mark.db


async def run(connection, build, cases, agent_dir: Path, **kwargs):
    return await sweep_.sweep(
        connection,
        build=build,
        spec=a_spec(),
        cases=cases,
        agents_root=agent_dir.parent,
        cycle_id=uuid4(),
        **kwargs,
    )


async def test_the_score_is_columns_and_a_failing_case_does_not_hide_the_passing_one(
    connection: asyncpg.Connection, build: Build, cases, agent_dir: Path
):
    swept = await run(connection, build, cases, agent_dir)

    # 9 columns across 3 cases: one clean, one wrong on two columns, one errored.
    assert (swept.passed, swept.total) == (4, 9)
    assert [(r.key, r.passed()) for r in swept.results] == [
        ("CAAU4056270", True),
        ("MNBU3974949", False),
        ("TTNU8982561", False),
    ]


async def test_a_case_that_raises_is_a_result_rather_than_the_end_of_the_sweep(
    connection: asyncpg.Connection, build: Build, cases, agent_dir: Path
):
    # An errored case is data. Letting it propagate would lose every case after
    # it, and the one that raised is often the least interesting of the three.
    swept = await run(connection, build, cases, agent_dir)

    errored = next(r for r in swept.results if r.key == "TTNU8982561")
    assert "workflow never returned" in (errored.errored or "")
    assert len(await evals_repo.results_for(connection, build.id)) == 3


async def test_the_trace_reaches_run_steps_with_the_step_names_as_primitive_keys(
    connection: asyncpg.Connection, build: Build, cases, agent_dir: Path
):
    await run(connection, build, cases, agent_dir)

    rows = await connection.fetch(
        "select s.seq, s.primitive_key, s.status from run_steps s "
        "join runs r on r.id = s.run_id join eval_cases c on c.id = r.case_id "
        "where c.key = 'CAAU4056270' order by s.seq"
    )
    assert [(r["primitive_key"], r["status"]) for r in rows] == [
        ("extract", "ok"),
        ("coas_valid", "ok"),
    ]


async def test_a_wrong_column_becomes_a_failure_addressed_to_the_file_that_fills_it(
    connection: asyncpg.Connection, build: Build, cases, agent_dir: Path
):
    await run(connection, build, cases, agent_dir)

    found = await evals_repo.failures_for(connection, build.id)
    output_diffs = {f["signature"] for f in found if f["detector"] == "output_diff"}
    assert output_diffs == {
        "coas_valid :: output_diff :: coa_success",
        "coas_valid :: output_diff :: failed_coa",
    }
    assert {f["primitive_key"] for f in found if f["detector"] == "output_diff"} == {"coas_valid"}


async def test_an_errored_case_produces_one_failure_and_not_one_per_column(
    connection: asyncpg.Connection, build: Build, cases, agent_dir: Path
):
    # Three columns are wrong for one reason. Three buckets would bury the
    # reason and inflate every count the loop ranks work by.
    await run(connection, build, cases, agent_dir)

    assertions = [
        f
        for f in await evals_repo.failures_for(connection, build.id)
        if f["detector"] == "assertion"
    ]
    assert len(assertions) == 1
    # Keyed on the exception TYPE. The message varies per case — a path, a
    # batch number — and a signature carrying one would put every case in a
    # bucket of its own, which is the opposite of bucketing.
    assert assertions[0]["signature"] == "entry_point :: assertion :: RuntimeError"


async def test_sweeping_the_same_build_twice_replaces_rather_than_accumulates(
    connection: asyncpg.Connection, build: Build, cases, agent_dir: Path
):
    # Re-running a build is ordinary — it is how you check a flake. Without a
    # clear, every count the gate and the curve compute doubles.
    await run(connection, build, cases, agent_dir)
    await run(connection, build, cases, agent_dir)

    assert len(await evals_repo.results_for(connection, build.id)) == 3


async def test_a_split_runs_only_its_own_cases(
    connection: asyncpg.Connection, build: Build, cases, agent_dir: Path, spec_id
):
    train = await evals_repo.cases_for(connection, spec_id, split="train")

    swept = await run(connection, build, train, agent_dir)

    assert [r.key for r in swept.results] == ["CAAU4056270", "MNBU3974949"]


async def test_every_case_writes_an_event_carrying_its_key(
    connection: asyncpg.Connection, build: Build, cases, agent_dir: Path
):
    cycle = uuid4()
    await sweep_.sweep(
        connection,
        build=build,
        spec=a_spec(),
        cases=cases,
        agents_root=agent_dir.parent,
        cycle_id=cycle,
    )

    rows = await connection.fetch(
        "select case_key, status from events where cycle_id = $1 and kind = 'case' order by id",
        cycle,
    )
    # Two events per case, and the order is the point: `started` lands before
    # the case runs, so a watcher can tell a slow sweep from a wedged one. With
    # only the terminal event, nothing at all appears until the first case
    # finishes — which on a live suite is minutes of silence.
    assert [(r["case_key"], r["status"]) for r in rows] == [
        ("CAAU4056270", "started"),
        ("CAAU4056270", "ok"),
        ("MNBU3974949", "started"),
        ("MNBU3974949", "failed"),
        ("TTNU8982561", "started"),
        ("TTNU8982561", "failed"),
    ]


async def test_a_step_name_that_is_not_a_card_key_is_dropped_and_reported(
    connection: asyncpg.Connection, build: Build, spec_id, agent_dir: Path
):
    # `run_steps.primitive_key` is the board_key domain, so a step called
    # "Check COAs" fails the insert. Losing the whole sweep over a trace problem
    # would throw away eval results that are perfectly good, and silently
    # slugifying the name would put a key in the table that names no card.
    case = await evals_repo.save_case(
        connection,
        spec_id,
        EvalCase(key="ODD", input={"produce": EXPECTED, "step": "Check COAs"}, expected_output={}),
    )
    (agent_dir / "src" / "entry.py").write_text(
        (agent_dir / "src" / "entry.py").read_text().replace('"coas_valid"', 'case["step"]')
    )

    swept = await run(connection, build, [case], agent_dir)

    assert swept.rejected_steps == ("Check COAs",)
    assert await connection.fetchval("select count(*) from run_steps") == 1


async def test_an_agent_whose_entry_point_will_not_import_fails_the_sweep_by_name(
    connection: asyncpg.Connection, build: Build, cases, agent_dir: Path
):
    # Not a per-case error: nothing ran, and reporting three identical import
    # failures would look like a per-case bug in the cases.
    (agent_dir / "src" / "entry.py").write_text("import nonexistent_module_xyz\n")

    with pytest.raises(sweep_.AgentNotRunnableError, match=r"src/entry\.py"):
        await run(connection, build, cases, agent_dir)


async def test_a_build_with_no_entry_point_says_so_rather_than_guessing(
    connection: asyncpg.Connection, spec_id, cases, agent_dir: Path
):
    build = await builds_repo.save(
        connection, Build(spec_id=spec_id, source_ref="agents/toy_prealert@deadbee")
    )

    with pytest.raises(sweep_.AgentNotRunnableError, match="entry point"):
        await run(connection, build, cases, agent_dir)
