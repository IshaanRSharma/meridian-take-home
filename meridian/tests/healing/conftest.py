"""A spec, a build and an agent on disk, so a sweep has something to sweep.

The agent is written into `tmp_path` rather than fixtured as an object, because
the thing under test is *loading one*: `build.json` names an entry point, the
sweep imports it, and everything about that — the path, the module name, the
import failing — only exists when there is a real file.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest

from meridian.domain.build import Build, EvalCase
from meridian.domain.frozen import FrozenSpec, ScopedContext, SpecPrimitive
from meridian.domain.primitives import CheckConfig, FieldRef, Fill
from meridian.repositories import builds as builds_repo
from meridian.repositories import evals as evals_repo
from meridian.repositories import specs as specs_repo

EXPECTED = {"coa_total": 5, "coa_success": 5, "failed_coa": 0}

# An agent that reads its answers out of the case. Deliberately not a workflow:
# these tests are about the harness — loading, comparing, storing — and a
# Temporal environment in every one of them would make each a slow test of
# something already proved once in `test_sweep_temporal.py`.
AGENT = '''
"""A stand-in agent. Returns what the case tells it to."""

from meridian.runtime.harness import CaseOutcome
from meridian.runtime.outcome import CheckResult, Failure
from meridian.runtime.trace import RunTrace


async def run_case(case):
    if case.get("raise"):
        raise RuntimeError(case["raise"])
    if case.get("hang"):
        # What a workflow task failure looks like from outside: Temporal logs the
        # traceback and retries forever, so the coroutine simply never returns.
        import asyncio
        import logging

        try:
            raise KeyError(case["hang"])
        except KeyError:
            logging.getLogger("temporalio.worker").exception("Failed activation")
        await asyncio.sleep(3600)

    trace = RunTrace(spec_version=1, spec_checksum="abc123")
    with trace.step("extract") as step:
        step.produced({"commercial_invoice": 1})
    if case.get("decline"):
        trace.decline(case["decline"], "matches no recognition rule")

    produced = case["produce"]
    failing = case.get("failing", [])
    with trace.step(case.get("step", "coas_valid")) as step:
        step.produced(
            CheckResult(
                outcome="pass" if not failing else "missing_coa",
                total=produced.get("coa_total", 0),
                passed=produced.get("coa_success", 0),
                failed=produced.get("failed_coa", 0),
                failures=tuple(
                    Failure(
                        grain="per_line_item",
                        locator=f"line_items[{i}]",
                        reason="no certificate carries this batch",
                        subject=value,
                        detail={"available": ["UAC25019", "UAC25020"]},
                    )
                    for i, value in enumerate(failing)
                ),
            )
        )

    return CaseOutcome.from_dump(produced, trace.dump())
'''


CONTEXT = ScopedContext(
    inherited=("[rule] One container is one shipment.",),
    local=(
        "[terminology] A batch is matched to a certificate by its batch number, "
        "not by the order the pages arrive in.",
    ),
    negative=(
        "Ignore a certificate whose batch appears on no invoice line — it "
        "belongs to a different shipment.",
    ),
    provenance=("53127e1d-4c8e-443d-af10-c7ae6efadcfc",),
)


def a_spec(slug: str = "toy_prealert", *, with_context: bool = False) -> FrozenSpec:
    """A spec whose one check fills all three columns the cases measure."""
    return FrozenSpec(
        name="Toy pre-alert",
        slug=slug,
        primitives={
            "coas_valid": SpecPrimitive(
                key="coas_valid",
                primitive_type="check",
                config=CheckConfig(
                    name="Does every batch have a certificate?",
                    fills=(
                        Fill(measure="checked", field=FieldRef(entity="summary", path="coa_total")),
                        Fill(
                            measure="passed", field=FieldRef(entity="summary", path="coa_success")
                        ),
                        Fill(measure="failed", field=FieldRef(entity="summary", path="failed_coa")),
                    ),
                ),
                context=CONTEXT if with_context else ScopedContext(),
            )
        },
    ).sealed()


@pytest.fixture
def agent_dir(tmp_path: Path) -> Path:
    """`agents/<slug>/` with an entry point and a build manifest."""
    where = tmp_path / "agents" / "toy_prealert"
    (where / "src" / "checks").mkdir(parents=True)
    (where / "src" / "checks" / "coas_valid.py").write_text("# the check\n")
    (where / "src" / "entry.py").write_text(AGENT)
    (where / "build.json").write_text(
        json.dumps(
            {
                "spec_checksum": a_spec().checksum,
                "entry_point": "src/entry.py",
                "model": "claude-opus-5",
                "prompt_version": "sha-abc",
                "temperature": 0.0,
                "file_map": {"coas_valid": "src/checks/coas_valid.py"},
            }
        )
    )
    return where


@pytest.fixture
async def spec_id(connection: asyncpg.Connection) -> UUID:
    board_id = await connection.fetchval(
        "insert into boards (name, status) values ('toy', 'submitted') returning id"
    )
    return await specs_repo.save(connection, a_spec().model_copy(update={"board_id": board_id}))


@pytest.fixture
async def build(connection: asyncpg.Connection, spec_id: UUID, agent_dir: Path) -> Build:
    return await builds_repo.save(
        connection,
        Build(
            spec_id=spec_id,
            source_ref="agents/toy_prealert@a3f9c21",
            created_by="codegen",
            model="claude-opus-5",
            file_map={"coas_valid": "src/checks/coas_valid.py"},
            entry_point="src/entry.py",
        ),
    )


@pytest.fixture
async def cases(connection: asyncpg.Connection, spec_id: UUID) -> tuple[EvalCase, ...]:
    """Three cases: one that passes, one that miscounts, one that raises."""
    written = []
    for case in (
        EvalCase(
            key="CAAU4056270",
            input={"produce": EXPECTED},
            expected_output=EXPECTED,
        ),
        EvalCase(
            key="MNBU3974949",
            input={"produce": {**EXPECTED, "coa_success": 3, "failed_coa": 2}},
            expected_output=EXPECTED,
            tags=("regression",),
        ),
        EvalCase(
            key="TTNU8982561",
            split="holdout",
            input={"raise": "workflow never returned"},
            expected_output=EXPECTED,
        ),
    ):
        written.append(await evals_repo.save_case(connection, spec_id, case))
    return tuple(written)
