"""Reading a skill's artifacts back in.

A skill has no database connection and should not have one — that is what makes
the loop runnable by anyone with the repo. So everything it did has to be
recoverable from two things it left behind: `build.json` and a git commit.

Every refusal here is about a row that would otherwise be a lie. A build
registered against the wrong spec, a `source_ref` naming a commit that does not
contain the code, a repair whose status was asserted rather than measured — each
one reads as true afterwards, which is what makes them worth refusing now.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from meridian import events
from meridian.domain.build import Build, EvalCase
from meridian.healing import bundle as bundle_
from meridian.healing import record
from meridian.healing import sweep as sweep_
from meridian.repositories import builds as builds_repo
from meridian.repositories import evals as evals_repo

from .conftest import EXPECTED, a_spec

pytestmark = pytest.mark.db

TARGET = "coas_valid :: output_diff :: coa_success"


def git(where: Path, *args: str) -> str:
    """Fixed argv, no shell, and every argument written in this file."""
    done = subprocess.run(  # noqa: S603 - a literal argv in a test fixture
        ["git", *args],  # noqa: S607 - git from PATH, as everywhere else in this repo
        cwd=where,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return done.stdout.strip()


@pytest.fixture
def repo(agent_dir: Path) -> Path:
    """A real repository with the agent committed. `agents/` tracked, as the loop needs."""
    root = agent_dir.parent.parent
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "test")
    # Importing the entry point writes bytecode beside it, and a committed .pyc
    # turns up in every `files_touched` as a file nobody edited.
    (root / ".gitignore").write_text("__pycache__/\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the first agent")
    return root


async def test_a_registered_build_carries_the_manifest_and_the_commit(
    connection: asyncpg.Connection, spec_id: UUID, repo: Path
):
    build = await record.register(
        connection, spec=a_spec(), spec_id=spec_id, repo_root=repo, cycle_id=uuid4()
    )

    assert build.iteration == 1
    assert build.source_ref == f"agents/toy_prealert@{git(repo, 'rev-parse', '--short', 'HEAD')}"
    assert build.file_map == {"coas_valid": "src/checks/coas_valid.py"}
    assert build.entry_point == "src/entry.py"
    assert build.model == "claude-opus-5"


async def test_a_second_registration_increments_and_links_its_parent(
    connection: asyncpg.Connection, spec_id: UUID, repo: Path
):
    # The curve's x axis, and the chain a repair walks back along. Both derived
    # from what is stored rather than supplied, so a retried registration cannot
    # assert a history the table disagrees with.
    first = await record.register(
        connection, spec=a_spec(), spec_id=spec_id, repo_root=repo, cycle_id=uuid4()
    )
    second = await record.register(
        connection,
        spec=a_spec(),
        spec_id=spec_id,
        repo_root=repo,
        cycle_id=uuid4(),
        created_by="repair",
    )

    assert (second.iteration, second.parent_build_id) == (2, first.id)
    assert second.created_by == "repair"


async def test_an_agent_built_from_a_different_spec_is_refused(
    connection: asyncpg.Connection, spec_id: UUID, repo: Path, agent_dir: Path
):
    # The mistake that would otherwise be invisible: the agent runs, the sweep
    # scores it, and every number is about a contract nobody approved.
    manifest = json.loads((agent_dir / "build.json").read_text())
    (agent_dir / "build.json").write_text(json.dumps(manifest | {"spec_checksum": "0" * 64}))

    with pytest.raises(record.NotRegisterableError, match="regenerate"):
        await record.register(
            connection, spec=a_spec(), spec_id=spec_id, repo_root=repo, cycle_id=uuid4()
        )


async def test_an_untracked_agent_is_refused_rather_than_given_a_sha_anyway(
    connection: asyncpg.Connection, spec_id: UUID, agent_dir: Path
):
    # A source_ref naming a commit that does not contain the code is worse than
    # none: it reads as recoverable, and `git checkout` on it hands back a
    # different program without complaining.
    root = agent_dir.parent.parent
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@e.com")
    git(root, "config", "user.name", "t")
    (root / "README").write_text("only this is committed\n")
    git(root, "add", "README")
    git(root, "commit", "-qm", "not the agent")

    with pytest.raises(record.NotRegisterableError, match="not tracked"):
        await record.register(
            connection, spec=a_spec(), spec_id=spec_id, repo_root=root, cycle_id=uuid4()
        )


async def test_a_missing_manifest_names_the_file_and_says_what_it_is_for(
    connection: asyncpg.Connection, spec_id: UUID, repo: Path, agent_dir: Path
):
    (agent_dir / "build.json").unlink()

    with pytest.raises(record.NotRegisterableError, match="file_map"):
        await record.register(
            connection, spec=a_spec(), spec_id=spec_id, repo_root=repo, cycle_id=uuid4()
        )


async def test_a_manifest_naming_an_entry_point_that_is_not_there_is_refused(
    connection: asyncpg.Connection, spec_id: UUID, repo: Path, agent_dir: Path
):
    manifest = json.loads((agent_dir / "build.json").read_text())
    (agent_dir / "build.json").write_text(json.dumps(manifest | {"entry_point": "src/gone.py"}))

    with pytest.raises(record.NotRegisterableError, match=r"src/gone\.py"):
        await record.register(
            connection, spec=a_spec(), spec_id=spec_id, repo_root=repo, cycle_id=uuid4()
        )


async def test_registering_writes_a_timeline_row_a_person_can_read(
    connection: asyncpg.Connection, spec_id: UUID, repo: Path
):
    # A human ran a skill in a terminal and the timeline still shows it. That is
    # what makes the UI a viewer of a headless system rather than the only way
    # to drive one.
    cycle = uuid4()
    await record.register(
        connection, spec=a_spec(), spec_id=spec_id, repo_root=repo, cycle_id=cycle
    )

    (row,) = await connection.fetch(
        "select phase, kind, detail from events where cycle_id = $1", cycle
    )
    assert (row["phase"], row["kind"]) == ("codegen", "build")
    assert events.detail_of(row)["model"] == "claude-opus-5"


# ── repairs ──────────────────────────────────────────────────────────────────


async def swept(  # noqa: PLR0913, PLR0917
    connection, build, spec_id, agent_dir, key, produced
) -> None:
    case = await evals_repo.save_case(
        connection,
        spec_id,
        EvalCase(key=key, input={"produce": produced}, expected_output=EXPECTED),
    )
    await sweep_.sweep(
        connection,
        build=build,
        spec=a_spec(),
        cases=[case],
        agents_root=agent_dir.parent,
        cycle_id=uuid4(),
    )


async def two_builds(  # noqa: PLR0913, PLR0917
    connection, spec_id, repo, agent_dir, before, after
) -> tuple[Build, Build]:
    first = await record.register(
        connection, spec=a_spec(), spec_id=spec_id, repo_root=repo, cycle_id=uuid4()
    )
    await swept(connection, first, spec_id, agent_dir, "CAAU4056270", before)

    (agent_dir / "src" / "checks" / "coas_valid.py").write_text("# patched\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "normalise batch numbers")

    second = await record.register(
        connection,
        spec=a_spec(),
        spec_id=spec_id,
        repo_root=repo,
        cycle_id=uuid4(),
        created_by="repair",
    )
    await swept(connection, second, spec_id, agent_dir, "CAAU4056270", after)
    return first, second


async def test_a_repair_the_gate_passes_stays_proposed_for_a_human_to_read(
    connection: asyncpg.Connection, spec_id: UUID, repo: Path, agent_dir: Path
):
    # The gate can only reject. Passing it is not approval — a person still
    # reads the diff, which is what `proposed` means.
    _, after = await two_builds(
        connection,
        spec_id,
        repo,
        agent_dir,
        {"coa_total": 5, "coa_success": 3, "failed_coa": 2},
        EXPECTED,
    )

    repair, verdict = await record.record_repair(
        connection,
        build=after,
        signature=TARGET,
        classification="implementation_defect",
        summary="normalised batch numbers before matching",
        cycle_id=uuid4(),
        repo_root=repo,
    )

    assert repair.status == "proposed"
    assert verdict is not None
    assert verdict.accepted


async def test_the_files_touched_come_from_the_commit_not_from_the_summary(
    connection: asyncpg.Connection, spec_id: UUID, repo: Path, agent_dir: Path
):
    # git is the transport for the diff. Nothing has to ask the skill what it
    # changed, which is what makes the record independent of the report.
    _, after = await two_builds(
        connection,
        spec_id,
        repo,
        agent_dir,
        {"coa_total": 5, "coa_success": 3, "failed_coa": 2},
        EXPECTED,
    )

    repair, _ = await record.record_repair(
        connection,
        build=after,
        signature=TARGET,
        classification="implementation_defect",
        summary="normalised batch numbers",
        cycle_id=uuid4(),
        repo_root=repo,
    )

    assert repair.files_touched == ("agents/toy_prealert/src/checks/coas_valid.py",)


async def test_the_failing_cases_are_read_from_the_sweep_rather_than_claimed(
    connection: asyncpg.Connection, spec_id: UUID, repo: Path, agent_dir: Path
):
    _, after = await two_builds(
        connection,
        spec_id,
        repo,
        agent_dir,
        {"coa_total": 5, "coa_success": 3, "failed_coa": 2},
        EXPECTED,
    )

    repair, _ = await record.record_repair(
        connection,
        build=after,
        signature=TARGET,
        classification="implementation_defect",
        summary="normalised batch numbers",
        cycle_id=uuid4(),
        repo_root=repo,
    )

    (case,) = await evals_repo.cases_for(connection, spec_id)
    assert repair.failing_case_ids == (case.id,)


async def test_a_patch_that_breaks_another_column_is_recorded_as_regressed(
    connection: asyncpg.Connection, spec_id: UUID, repo: Path, agent_dir: Path
):
    _, after = await two_builds(
        connection,
        spec_id,
        repo,
        agent_dir,
        {"coa_total": 5, "coa_success": 3, "failed_coa": 2},
        {"coa_total": 6, "coa_success": 5, "failed_coa": 1},
    )

    repair, verdict = await record.record_repair(
        connection,
        build=after,
        signature=TARGET,
        classification="implementation_defect",
        summary="widened the match",
        cycle_id=uuid4(),
        repo_root=repo,
    )

    assert repair.status == "regressed"
    assert verdict is not None
    assert verdict.regressed == (("CAAU4056270", "coa_total"),)
    assert repair.regressed_case_ids != ()


async def test_a_spec_gap_is_escalated_and_the_table_insists_on_a_thread(
    connection: asyncpg.Connection, spec_id: UUID, repo: Path, agent_dir: Path
):
    # A decision nobody can settle in code goes back to the process owner. The
    # constraint is on the table so it holds for every caller, not only for the
    # ones that came through here.
    _, after = await two_builds(
        connection,
        spec_id,
        repo,
        agent_dir,
        {"coa_total": 5, "coa_success": 3, "failed_coa": 2},
        {"coa_total": 5, "coa_success": 3, "failed_coa": 2},
    )

    with pytest.raises(asyncpg.PostgresError):
        await record.record_repair(
            connection,
            build=after,
            signature=TARGET,
            classification="spec_gap",
            summary="a certificate whose batch is on no invoice: ignore or flag?",
            cycle_id=uuid4(),
            repo_root=repo,
        )


async def test_a_spec_gap_with_a_thread_is_escalated_and_never_gated(
    connection: asyncpg.Connection, spec_id: UUID, repo: Path, agent_dir: Path
):
    # Nothing to measure: no patch is correct because no rule decides the
    # answer. Running the gate would produce a verdict about the wrong question.
    _, after = await two_builds(
        connection,
        spec_id,
        repo,
        agent_dir,
        {"coa_total": 5, "coa_success": 3, "failed_coa": 2},
        {"coa_total": 5, "coa_success": 3, "failed_coa": 2},
    )
    board_id = await connection.fetchval("select board_id from specs limit 1")
    thread_id = await connection.fetchval(
        "insert into threads (board_id, category, question) "
        "values ($1, 'spec_gap', 'ignore or flag?') returning id",
        board_id,
    )

    repair, verdict = await record.record_repair(
        connection,
        build=after,
        signature=TARGET,
        classification="spec_gap",
        summary="a certificate whose batch is on no invoice: ignore or flag?",
        cycle_id=uuid4(),
        repo_root=repo,
        thread_id=thread_id,
    )

    assert repair.status == "escalated"
    assert verdict is None


async def test_a_repair_against_a_build_with_no_parent_says_so(
    connection: asyncpg.Connection, spec_id: UUID, repo: Path, agent_dir: Path
):
    first = await record.register(
        connection, spec=a_spec(), spec_id=spec_id, repo_root=repo, cycle_id=uuid4()
    )

    with pytest.raises(record.NotRegisterableError, match="nothing to have repaired"):
        await record.record_repair(
            connection,
            build=first,
            signature=TARGET,
            classification="implementation_defect",
            summary="a patch against nothing",
            cycle_id=uuid4(),
            repo_root=repo,
        )


async def test_a_recorded_repair_reaches_the_bundle_as_history(
    connection: asyncpg.Connection, spec_id: UUID, repo: Path, agent_dir: Path
):
    # The loop closing. Without this the next session retries the same idea,
    # which is the specific waste a human-run loop suffers.
    before, after = await two_builds(
        connection,
        spec_id,
        repo,
        agent_dir,
        {"coa_total": 5, "coa_success": 3, "failed_coa": 2},
        {"coa_total": 6, "coa_success": 5, "failed_coa": 1},
    )
    await record.record_repair(
        connection,
        build=after,
        signature=TARGET,
        classification="implementation_defect",
        summary="widened the match",
        cycle_id=uuid4(),
        repo_root=repo,
    )

    text = await bundle_.bundle(connection, build=before, spec=a_spec(), signature=TARGET)

    assert "widened the match" in text
    assert "[regressed]" in text


async def test_builds_repo_orders_the_curve_by_iteration(
    connection: asyncpg.Connection, spec_id: UUID, repo: Path
):
    for _ in range(3):
        await record.register(
            connection, spec=a_spec(), spec_id=spec_id, repo_root=repo, cycle_id=uuid4()
        )

    assert [b.iteration for b in await builds_repo.for_spec(connection, spec_id)] == [1, 2, 3]


async def test_a_build_measured_outside_agents_still_knows_its_own_slug(
    connection: asyncpg.Connection, spec_id: UUID, repo: Path, agent_dir: Path
):
    # The agents root is a parameter — a candidate build gets measured somewhere
    # other than the shelf. Stripping a fixed `agents/` prefix would leave the
    # root inside the slug, and every path built from it would double it.
    moved = repo / "candidates"
    moved.mkdir()
    (agent_dir.parent.parent / "agents" / "toy_prealert").rename(moved / "toy_prealert")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "move the candidate")

    build = await record.register(
        connection,
        spec=a_spec(),
        spec_id=spec_id,
        repo_root=repo,
        cycle_id=uuid4(),
        agents_dir="candidates",
    )

    assert build.source_ref.startswith("candidates/toy_prealert@")
    assert build.slug() == "toy_prealert"
