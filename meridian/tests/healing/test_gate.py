"""Target passes AND nothing that passed before now fails.

The gate is the one automatic decision in the system, and it can only **reject**
— a human is required to override it and never to approve in its place. So the
properties that matter are the ways it could wrongly accept.

The sharpest is per-column. A patch that fixes `coa_success` and breaks
`failed_coa` leaves both cases failing before and after, so a gate comparing
rows sees nothing happen and lets the regression through. Every test here is
built so that a row-level gate would give a different answer.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from meridian.domain.build import Build, EvalCase
from meridian.healing import sweep as sweep_
from meridian.healing.gate import gate
from meridian.repositories import builds as builds_repo
from meridian.repositories import evals as evals_repo

from .conftest import EXPECTED, a_spec

pytestmark = pytest.mark.db

TARGET = "coas_valid :: output_diff :: coa_success"


async def a_build(connection, spec_id) -> Build:
    return await builds_repo.save(
        connection,
        Build(
            spec_id=spec_id,
            source_ref="agents/toy_prealert@a3f9c21",
            created_by="repair",
            file_map={"coas_valid": "src/checks/coas_valid.py"},
            entry_point="src/entry.py",
        ),
    )


async def sweep_producing(connection, build, spec_id, agent_dir, produced: dict) -> None:
    """One case, whose agent produces exactly what the test asks for."""
    case = await evals_repo.save_case(
        connection,
        spec_id,
        EvalCase(key="CAAU4056270", input={"produce": produced}, expected_output=EXPECTED),
    )
    await sweep_.sweep(
        connection,
        build=build,
        spec=a_spec(),
        cases=[case],
        agents_root=agent_dir.parent,
        cycle_id=uuid4(),
    )


async def test_a_patch_that_fixes_the_target_and_moves_nothing_else_is_accepted(
    connection: asyncpg.Connection, build: Build, spec_id, agent_dir: Path
):
    await sweep_producing(
        connection, build, spec_id, agent_dir, {"coa_total": 5, "coa_success": 3, "failed_coa": 2}
    )
    after = await a_build(connection, spec_id)
    await sweep_producing(connection, after, spec_id, agent_dir, EXPECTED)

    verdict = await gate(connection, before=build, after=after, signature=TARGET)

    assert verdict.accepted
    assert verdict.fixed == ("CAAU4056270",)
    assert verdict.regressed == ()


async def test_a_patch_that_fixes_one_column_and_breaks_another_is_rejected(
    connection: asyncpg.Connection, build: Build, spec_id, agent_dir: Path
):
    # The case a row-level gate cannot see. The row failed before and fails
    # after, so nothing about it changed at row granularity — and a real
    # regression went past.
    await sweep_producing(
        connection, build, spec_id, agent_dir, {"coa_total": 5, "coa_success": 3, "failed_coa": 2}
    )
    after = await a_build(connection, spec_id)
    await sweep_producing(
        connection, after, spec_id, agent_dir, {"coa_total": 6, "coa_success": 5, "failed_coa": 1}
    )

    verdict = await gate(connection, before=build, after=after, signature=TARGET)

    assert not verdict.accepted
    assert verdict.regressed == (("CAAU4056270", "coa_total"),)
    assert "coa_total" in verdict.reason


async def test_a_patch_that_does_not_fix_the_target_is_rejected(
    connection: asyncpg.Connection, build: Build, spec_id, agent_dir: Path
):
    await sweep_producing(
        connection, build, spec_id, agent_dir, {"coa_total": 5, "coa_success": 3, "failed_coa": 2}
    )
    after = await a_build(connection, spec_id)
    await sweep_producing(
        connection, after, spec_id, agent_dir, {"coa_total": 5, "coa_success": 4, "failed_coa": 1}
    )

    verdict = await gate(connection, before=build, after=after, signature=TARGET)

    assert not verdict.accepted
    assert verdict.fixed == ()
    assert "still failing" in verdict.reason


async def test_a_column_that_was_already_failing_is_not_a_regression(
    connection: asyncpg.Connection, build: Build, spec_id, agent_dir: Path
):
    # Only a column that PASSED before and fails now. Counting one that was
    # already broken would make every partial fix look like a regression, and
    # nothing would ever pass the gate.
    await sweep_producing(
        connection, build, spec_id, agent_dir, {"coa_total": 6, "coa_success": 3, "failed_coa": 3}
    )
    after = await a_build(connection, spec_id)
    await sweep_producing(
        connection, after, spec_id, agent_dir, {"coa_total": 6, "coa_success": 5, "failed_coa": 1}
    )

    verdict = await gate(connection, before=build, after=after, signature=TARGET)

    # `coa_total` was wrong before and is wrong now — untouched, not regressed.
    assert verdict.regressed == ()
    assert verdict.accepted


async def test_a_case_the_earlier_build_never_ran_cannot_regress(
    connection: asyncpg.Connection, build: Build, spec_id, agent_dir: Path
):
    # A case added between builds has no "before" to have passed in. Treating
    # its failure as a regression would blame a patch for a case it never saw.
    await sweep_producing(
        connection, build, spec_id, agent_dir, {"coa_total": 5, "coa_success": 3, "failed_coa": 2}
    )
    after = await a_build(connection, spec_id)
    # Fails, but on other columns — so the target half of the gate stays clean
    # and only the regression half is under test.
    fresh = await evals_repo.save_case(
        connection,
        spec_id,
        EvalCase(
            key="NEW",
            input={"produce": {"coa_total": 6, "coa_success": 5, "failed_coa": 1}},
            expected_output=EXPECTED,
        ),
    )
    old = await evals_repo.save_case(
        connection,
        spec_id,
        EvalCase(key="CAAU4056270", input={"produce": EXPECTED}, expected_output=EXPECTED),
    )
    await sweep_.sweep(
        connection,
        build=after,
        spec=a_spec(),
        cases=[old, fresh],
        agents_root=agent_dir.parent,
        cycle_id=uuid4(),
    )

    verdict = await gate(connection, before=build, after=after, signature=TARGET)

    assert verdict.regressed == ()
    assert verdict.accepted


async def test_a_new_case_failing_the_target_signature_still_rejects(
    connection: asyncpg.Connection, build: Build, spec_id, agent_dir: Path
):
    # The other half of the same situation, and it goes the other way. A case
    # the patch never saw cannot have *regressed* — but if it fails the very
    # signature the patch claims to have fixed, the claim is not yet true.
    # Rejecting is safe: the gate can only reject, and a human can override.
    await sweep_producing(
        connection, build, spec_id, agent_dir, {"coa_total": 5, "coa_success": 3, "failed_coa": 2}
    )
    after = await a_build(connection, spec_id)
    old = await evals_repo.save_case(
        connection,
        spec_id,
        EvalCase(key="CAAU4056270", input={"produce": EXPECTED}, expected_output=EXPECTED),
    )
    fresh = await evals_repo.save_case(
        connection,
        spec_id,
        EvalCase(
            key="NEW",
            input={"produce": {"coa_total": 9, "coa_success": 9, "failed_coa": 0}},
            expected_output=EXPECTED,
        ),
    )
    await sweep_.sweep(
        connection,
        build=after,
        spec=a_spec(),
        cases=[old, fresh],
        agents_root=agent_dir.parent,
        cycle_id=uuid4(),
    )

    verdict = await gate(connection, before=build, after=after, signature=TARGET)

    assert not verdict.accepted
    assert verdict.fixed == ("CAAU4056270",)
    assert "NEW" in verdict.reason


async def test_a_case_that_errors_after_passing_is_a_regression_on_every_column(
    connection: asyncpg.Connection, build: Build, spec_id, agent_dir: Path
):
    # The loudest kind. A patch that makes a working case raise has broken
    # everything that case measured, and reporting it as one error would rank it
    # below a two-column miscount.
    await sweep_producing(connection, build, spec_id, agent_dir, EXPECTED)
    after = await a_build(connection, spec_id)
    broken = await evals_repo.save_case(
        connection,
        spec_id,
        EvalCase(
            key="CAAU4056270", input={"raise": "boom", "produce": {}}, expected_output=EXPECTED
        ),
    )
    await sweep_.sweep(
        connection,
        build=after,
        spec=a_spec(),
        cases=[broken],
        agents_root=agent_dir.parent,
        cycle_id=uuid4(),
    )

    verdict = await gate(connection, before=build, after=after, signature=TARGET)

    assert not verdict.accepted
    assert len(verdict.regressed) == len(EXPECTED)


async def test_a_build_with_no_sweep_is_refused_rather_than_treated_as_perfect(
    connection: asyncpg.Connection, build: Build, spec_id, agent_dir: Path
):
    # An unswept build has no failing signature, which reads as "the target
    # passes" — and no columns, which reads as "nothing regressed". Both are
    # true of a build nobody measured, and together they would accept anything.
    await sweep_producing(
        connection, build, spec_id, agent_dir, {"coa_total": 5, "coa_success": 3, "failed_coa": 2}
    )
    after = await a_build(connection, spec_id)

    verdict = await gate(connection, before=build, after=after, signature=TARGET)

    assert not verdict.accepted
    assert "no sweep" in verdict.reason


async def test_a_narrower_sweep_is_refused_rather_than_read_as_no_regression(
    connection: asyncpg.Connection, build: Build, spec_id, agent_dir: Path
):
    """Measuring fewer cases than the baseline is not evidence of no regression.

    Working case by case is the right way to run this loop — each eval case is a
    different shape, and sweeping all of them averages the shapes together. But
    it only stays safe while the set grows. A case dropped from the new sweep is
    absent rather than failing, and absent reads as "did not regress", so a
    patch measured on one case could sail past a baseline of nine.

    The gate refuses instead of trusting whoever ran it, because the whole point
    of the gate is that it does not depend on discipline.
    """
    old = await evals_repo.save_case(
        connection,
        spec_id,
        EvalCase(key="CAAU4056270", input={"produce": EXPECTED}, expected_output=EXPECTED),
    )
    other = await evals_repo.save_case(
        connection,
        spec_id,
        EvalCase(key="MNBU3974949", input={"produce": EXPECTED}, expected_output=EXPECTED),
    )
    await sweep_.sweep(
        connection,
        build=build,
        spec=a_spec(),
        cases=[old, other],
        agents_root=agent_dir.parent,
        cycle_id=uuid4(),
    )

    after = await a_build(connection, spec_id)
    await sweep_.sweep(
        connection,
        build=after,
        spec=a_spec(),
        cases=[old],
        agents_root=agent_dir.parent,
        cycle_id=uuid4(),
    )

    verdict = await gate(connection, before=build, after=after, signature=TARGET)

    assert not verdict.accepted
    assert "MNBU3974949" in verdict.reason
