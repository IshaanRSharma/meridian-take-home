"""A process this loop has never seen, all the way through.

Everything else in this suite runs the pre-alert board, and every worked example
in `healing/` is written about it. That is exactly how a generic pipeline
quietly becomes a specific one: nothing has to be hardcoded for the *tests* to
be, and then the second customer's board is the thing that finds out.

So this is clinician credentialing — different entities, different columns,
different criterion, different grain, an outcome the pre-alert board has no
equivalent of — driven through the whole loop: sweep, score, localise, bundle,
gate. No module here is allowed to need a shipment.

The property in one line: **a new board, frozen and generated, produces evals.**
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from meridian.domain.build import Build, EvalCase
from meridian.domain.frozen import FrozenSpec, ScopedContext, SpecPrimitive
from meridian.domain.primitives import CheckConfig, FieldRef, Fill
from meridian.healing import bundle as bundle_
from meridian.healing import record
from meridian.healing import sweep as sweep_
from meridian.healing.gate import gate
from meridian.repositories import builds as builds_repo
from meridian.repositories import evals as evals_repo
from meridian.repositories import specs as specs_repo

pytestmark = pytest.mark.db

# One row per credentialing application: how many licences were checked against
# the state board, how many came back current, how many did not.
EXPECTED = {"licences_checked": 4, "licences_current": 4, "licences_lapsed": 0}

AGENT = '''
"""A credentialing agent. Nothing about it resembles a pre-alert."""

from meridian.runtime.harness import CaseOutcome
from meridian.runtime.outcome import CheckResult, Failure
from meridian.runtime.trace import RunTrace


async def run_case(case):
    trace = RunTrace(spec_version=1, spec_checksum="cred1")
    with trace.step("fetch_board_record") as step:
        step.produced({"state_board_record": len(case["licences"])})
    if case.get("skipped"):
        trace.decline(case["skipped"], "the state board portal returned nothing")

    lapsed = [lic for lic in case["licences"] if lic["status"] != "current"]
    with trace.step("licence_current") as step:
        step.produced(
            CheckResult(
                outcome="pass" if not lapsed else "licence_lapsed",
                total=len(case["licences"]),
                passed=len(case["licences"]) - len(lapsed),
                failed=len(lapsed),
                failures=tuple(
                    Failure(
                        grain="per_line_item",
                        locator=f"licences[{i}]",
                        reason="not current with the state board",
                        subject=lic["number"],
                        detail={"expires": lic.get("expires")},
                    )
                    for i, lic in enumerate(lapsed)
                ),
            )
        )

    row = {
        "licences_checked": len(case["licences"]),
        "licences_current": len(case["licences"]) - len(lapsed),
        "licences_lapsed": len(lapsed),
    }
    return CaseOutcome.from_dump(row, trace.dump())
'''


def credentialing() -> FrozenSpec:
    """A board with nothing in common with the running example."""
    return FrozenSpec(
        name="Clinician credentialing",
        slug="clinician_credentialing",
        primitives={
            "licence_current": SpecPrimitive(
                key="licence_current",
                primitive_type="check",
                config=CheckConfig(
                    name="Is every licence current with its state board?",
                    scope="per_line_item",
                    fills=(
                        Fill(
                            measure="checked",
                            field=FieldRef(entity="application_summary", path="licences_checked"),
                        ),
                        Fill(
                            measure="passed",
                            field=FieldRef(entity="application_summary", path="licences_current"),
                        ),
                        Fill(
                            measure="failed",
                            field=FieldRef(entity="application_summary", path="licences_lapsed"),
                        ),
                    ),
                ),
                context=ScopedContext(
                    local=("[rule] A licence lapses on its expiry date, not on renewal.",),
                    negative=("Do not treat a pending renewal as current.",),
                ),
            )
        },
    ).sealed()


@pytest.fixture
def credentialing_agent(tmp_path: Path) -> Path:
    where = tmp_path / "agents" / "clinician_credentialing"
    (where / "checks").mkdir(parents=True)
    (where / "checks" / "licence_current.py").write_text("# the check\n")
    (where / "agent.py").write_text(AGENT)
    (where / "build.json").write_text(
        json.dumps(
            {
                "spec_checksum": credentialing().checksum,
                "entry_point": "agent.py",
                "file_map": {"licence_current": "checks/licence_current.py"},
            }
        )
    )
    return where


@pytest.fixture
async def credentialing_spec(connection: asyncpg.Connection) -> UUID:
    board_id = await connection.fetchval(
        "insert into boards (name, status) values ('credentialing', 'submitted') returning id"
    )
    return await specs_repo.save(
        connection, credentialing().model_copy(update={"board_id": board_id})
    )


@pytest.fixture
async def credentialing_build(
    connection: asyncpg.Connection, credentialing_spec: UUID, credentialing_agent: Path
) -> Build:
    return await builds_repo.save(
        connection,
        Build(
            spec_id=credentialing_spec,
            source_ref="agents/clinician_credentialing@a1b2c3d",
            file_map={"licence_current": "checks/licence_current.py"},
            entry_point="agent.py",
        ),
    )


def licence(number: str, status: str = "current") -> dict[str, str]:
    return {"number": number, "status": status, "expires": "2027-01-31"}


async def load(connection: asyncpg.Connection, spec_id: UUID, *cases: EvalCase) -> list[EvalCase]:
    return [await evals_repo.save_case(connection, spec_id, case) for case in cases]


async def test_a_process_this_loop_has_never_seen_produces_evals(
    connection: asyncpg.Connection,
    credentialing_build: Build,
    credentialing_spec: UUID,
    credentialing_agent: Path,
):
    cases = await load(
        connection,
        credentialing_spec,
        EvalCase(
            key="APP-8812",
            input={"licences": [licence(f"RN{i}") for i in range(4)]},
            expected_output=EXPECTED,
        ),
        EvalCase(
            key="APP-9034",
            input={
                "licences": [licence("RN9"), licence("RN10", "lapsed")],
                "skipped": "texas-portal",
            },
            expected_output={
                "licences_checked": 2,
                "licences_current": 2,
                "licences_lapsed": 0,
            },
        ),
    )

    swept = await sweep_.sweep(
        connection,
        build=credentialing_build,
        spec=credentialing(),
        cases=cases,
        agents_root=credentialing_agent.parent,
        cycle_id=uuid4(),
    )

    assert swept.errored() == ()
    assert not swept.empty
    # Six columns across two cases; the second miscounts two of its three.
    assert (swept.passed, swept.total) == (4, 6)


async def test_its_failure_localises_to_its_own_file_and_its_own_column(
    connection: asyncpg.Connection,
    credentialing_build: Build,
    credentialing_spec: UUID,
    credentialing_agent: Path,
):
    # The chain is column -> the primitive whose fills target it -> file_map.
    # Nothing in it knows what a shipment is.
    cases = await load(
        connection,
        credentialing_spec,
        EvalCase(
            key="APP-9034",
            input={"licences": [licence("RN9"), licence("RN10", "lapsed")]},
            expected_output={"licences_checked": 2, "licences_current": 2, "licences_lapsed": 0},
        ),
    )
    await sweep_.sweep(
        connection,
        build=credentialing_build,
        spec=credentialing(),
        cases=cases,
        agents_root=credentialing_agent.parent,
        cycle_id=uuid4(),
    )

    found = await evals_repo.failures_for(connection, credentialing_build.identity)
    assert {f["signature"] for f in found} == {
        "licence_current :: output_diff :: licences_current",
        "licence_current :: output_diff :: licences_lapsed",
    }
    assert {f["primitive_key"] for f in found} == {"licence_current"}


async def test_its_bundle_carries_its_own_evidence_and_its_own_settled_knowledge(
    connection: asyncpg.Connection,
    credentialing_build: Build,
    credentialing_spec: UUID,
    credentialing_agent: Path,
):
    cases = await load(
        connection,
        credentialing_spec,
        EvalCase(
            key="APP-9034",
            input={
                "licences": [licence("RN9"), licence("RN10", "lapsed")],
                "skipped": "texas-portal",
            },
            expected_output={"licences_checked": 2, "licences_current": 2, "licences_lapsed": 0},
        ),
    )
    await sweep_.sweep(
        connection,
        build=credentialing_build,
        spec=credentialing(),
        cases=cases,
        agents_root=credentialing_agent.parent,
        cycle_id=uuid4(),
    )

    text = await bundle_.bundle(
        connection, build=credentialing_build, spec=credentialing(), signature=None
    )

    assert "agents/clinician_credentialing/checks/licence_current.py" in text
    assert "RN10" in text  # the licence that failed, by name
    assert "texas-portal" in text  # what was skipped, and why
    assert "A licence lapses on its expiry date" in text  # settled knowledge, inlined
    assert "Do not treat a pending renewal as current" in text


async def test_its_gate_judges_a_patch_the_same_way(
    connection: asyncpg.Connection,
    credentialing_build: Build,
    credentialing_spec: UUID,
    credentialing_agent: Path,
):
    wrong = {"licences_checked": 2, "licences_current": 2, "licences_lapsed": 0}
    before = await load(
        connection,
        credentialing_spec,
        EvalCase(
            key="APP-9034",
            input={"licences": [licence("RN9"), licence("RN10", "lapsed")]},
            expected_output=wrong,
        ),
    )
    await sweep_.sweep(
        connection,
        build=credentialing_build,
        spec=credentialing(),
        cases=before,
        agents_root=credentialing_agent.parent,
        cycle_id=uuid4(),
    )

    after = await builds_repo.save(
        connection,
        Build(
            spec_id=credentialing_spec,
            source_ref="agents/clinician_credentialing@d4e5f6a",
            created_by="repair",
            file_map={"licence_current": "checks/licence_current.py"},
            entry_point="agent.py",
        ),
    )
    fixed = await load(
        connection,
        credentialing_spec,
        EvalCase(
            key="APP-9034",
            input={"licences": [licence("RN9"), licence("RN10")]},
            expected_output=wrong,
        ),
    )
    await sweep_.sweep(
        connection,
        build=after,
        spec=credentialing(),
        cases=fixed,
        agents_root=credentialing_agent.parent,
        cycle_id=uuid4(),
    )

    verdict = await gate(
        connection,
        before=credentialing_build,
        after=after,
        signature="licence_current :: output_diff :: licences_current",
    )

    assert verdict.accepted
    assert verdict.fixed == ("APP-9034",)


async def test_a_column_this_board_fills_nowhere_is_reported_before_anything_is_built(
    connection: asyncpg.Connection, credentialing_spec: UUID
):
    # The one way a new board silently produces no useful evals: an expected
    # column no primitive fills. The sweep still scores it — as a permanent
    # failure nothing can be localised to — so it is worth saying at load time,
    # while the board can still be edited.
    await load(
        connection,
        credentialing_spec,
        EvalCase(
            key="APP-1",
            input={"licences": []},
            expected_output=EXPECTED | {"sanctions_found": 0},
        ),
    )

    unfilled = await record.unattributable_columns(
        connection, spec=credentialing(), spec_id=credentialing_spec
    )

    assert unfilled == ("sanctions_found",)
