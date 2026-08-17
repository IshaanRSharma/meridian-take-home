"""The single emit, and the one property that makes it worth having.

`events` is the only table the browser subscribes to, so it is the whole reason
a pipeline call can return a `cycle_id` immediately instead of blocking. What
makes that work is not the insert — it is that **one cycle_id correlates a run
end to end**, across phases that different processes wrote. A command that emits
under a fresh id each time produces rows nothing can assemble into a timeline.

Marked `db` throughout: the phase and status vocabularies are check constraints,
and a test that stubbed the database would be checking a dictionary against
itself rather than against the schema that will actually refuse the write.
"""

from __future__ import annotations

from uuid import uuid4

import asyncpg
import pytest

from meridian import events

pytestmark = pytest.mark.db


async def rows(connection: asyncpg.Connection, cycle_id):
    return await connection.fetch(
        "select phase, kind, status, case_key, detail, duration_ms from events "
        "where cycle_id = $1 order by id",
        cycle_id,
    )


async def test_one_cycle_correlates_rows_across_phases(connection: asyncpg.Connection):
    # The property the table exists for. Codegen, eval and repair are three
    # processes and a person ran two of them by hand; the timeline is only a
    # timeline because they agreed on an id.
    cycle = uuid4()
    await events.emit(connection, cycle_id=cycle, phase="codegen", kind="build", status="ok")
    await events.emit(connection, cycle_id=cycle, phase="eval", kind="sweep", status="started")
    await events.emit(connection, cycle_id=cycle, phase="repair", kind="patch", status="rejected")

    assert [(r["phase"], r["kind"], r["status"]) for r in await rows(connection, cycle)] == [
        ("codegen", "build", "ok"),
        ("eval", "sweep", "started"),
        ("repair", "patch", "rejected"),
    ]


async def test_a_case_key_travels_with_the_row(connection: asyncpg.Connection):
    # Without it the eval phase is a count. With it the timeline says which
    # shipment was being run when something failed.
    cycle = uuid4()
    await events.emit(
        connection,
        cycle_id=cycle,
        phase="eval",
        kind="case",
        status="failed",
        case_key="CAAU4056270",
        detail={"mismatched": ["coa_success"]},
    )

    (row,) = await rows(connection, cycle)
    assert row["case_key"] == "CAAU4056270"
    assert events.detail_of(row) == {"mismatched": ["coa_success"]}


async def test_a_phase_outside_the_vocabulary_is_refused(connection: asyncpg.Connection):
    # The check constraint is the authority, not this module. A typo'd phase
    # that inserted would be a row no view ever selects — silent, and worse
    # than a failure.
    with pytest.raises(asyncpg.PostgresError):
        await events.emit(
            connection,
            cycle_id=uuid4(),
            phase="sweeping",  # type: ignore[arg-type]
            kind="sweep",
            status="ok",
        )


async def test_timing_a_phase_records_started_then_ok(connection: asyncpg.Connection):
    cycle = uuid4()
    async with events.during(connection, cycle_id=cycle, phase="eval", kind="sweep"):
        pass

    written = await rows(connection, cycle)
    assert [r["status"] for r in written] == ["started", "ok"]
    assert written[1]["duration_ms"] is not None


async def test_timing_a_phase_that_raises_records_the_failure_and_re_raises(
    connection: asyncpg.Connection,
):
    # The closing row must not depend on the body succeeding, or the timeline
    # shows every failure as a phase that started and never ended — which reads
    # as "still running" rather than "broke".
    cycle = uuid4()
    with pytest.raises(RuntimeError):
        async with events.during(connection, cycle_id=cycle, phase="eval", kind="sweep"):
            raise RuntimeError("temporal never returned")

    written = await rows(connection, cycle)
    assert [r["status"] for r in written] == ["started", "failed"]
    assert "temporal never returned" in events.detail_of(written[1])["error"]


async def test_the_body_can_add_detail_to_the_closing_row(connection: asyncpg.Connection):
    # A sweep knows its score only once it has finished, and the score is the
    # single most useful thing in the timeline.
    cycle = uuid4()
    async with events.during(connection, cycle_id=cycle, phase="eval", kind="sweep") as closing:
        closing["passed"], closing["total"] = 19, 21

    written = await rows(connection, cycle)
    assert events.detail_of(written[1]) == {"passed": 19, "total": 21}
