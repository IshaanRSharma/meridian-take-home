"""The block somebody pastes, and the one property that decides whether it works.

**Nothing in it may require a further lookup.** That is the whole deliverable —
not the automation. A bundle naming a run id, a case id or a signature the
reader has to go and resolve has sent them back to a database they were supposed
not to need, and at that point they may as well have read the failure themselves.

So the tests are mostly assertions about what a reader can see without leaving
the paste: the file, the values that disagreed, the values that failed, what was
skipped, what was already settled about this step, and what has already been
tried against this signature.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from meridian.domain.build import Build, EvalCase, Repair
from meridian.healing import bundle as bundle_
from meridian.healing import sweep as sweep_
from meridian.repositories import evals as evals_repo
from meridian.repositories import repairs as repairs_repo

from .conftest import EXPECTED, a_spec

pytestmark = pytest.mark.db

# `UAC25022 ` has a trailing space and `uac25019` is lowercased. Nobody has to
# be told that is the bug — which is the point of carrying the values at all.
UNMATCHED = ["UAC25022 ", "uac25019"]


@pytest.fixture
async def swept(connection: asyncpg.Connection, build: Build, spec_id: UUID, agent_dir: Path):
    """One sweep with a real failure, its evidence and a declined attachment."""
    case = await evals_repo.save_case(
        connection,
        spec_id,
        EvalCase(
            key="CAAU4056270",
            input={
                "produce": {**EXPECTED, "coa_success": 3, "failed_coa": 2},
                "failing": UNMATCHED,
                "decline": "image001.gif",
            },
            expected_output=EXPECTED,
        ),
    )
    return await sweep_.sweep(
        connection,
        build=build,
        spec=a_spec(),
        cases=[case],
        agents_root=agent_dir.parent,
        cycle_id=uuid4(),
    )


async def rendered(connection, build, **kwargs) -> str:
    return await bundle_.bundle(connection, build=build, spec=a_spec(), **kwargs)


async def test_the_header_says_which_build_and_how_it_scored(
    connection: asyncpg.Connection, build: Build, swept
):
    text = await rendered(connection, build)

    assert f"BUILD {build.iteration}" in text
    assert "1/3" in text


async def test_it_names_the_signature_and_how_many_cases_share_it(
    connection: asyncpg.Connection, build: Build, swept
):
    text = await rendered(connection, build)

    assert "coas_valid :: output_diff ::" in text
    assert "(1 case)" in text


async def test_it_names_the_file_to_open(connection: asyncpg.Connection, build: Build, swept):
    # Resolved from build.json's file_map through the spec's slug. The whole
    # reason `file_map` is a column rather than a comment convention.
    text = await rendered(connection, build)

    assert "agents/toy_prealert/src/checks/coas_valid.py" in text
    assert "spec.lock.json § primitives.coas_valid" in text


async def test_it_shows_only_the_columns_that_disagreed(
    connection: asyncpg.Connection, build: Build, swept
):
    # Printing all seven would bury the two that moved. `coa_total` agreed and
    # has nothing to contribute.
    text = await rendered(connection, build, signature="coas_valid :: output_diff :: coa_success")

    assert re.search(r"coa_success\s+expected\s+5\s+actual\s+3", text)
    assert "coa_total" not in text.split("SPEC CONTEXT")[0]


async def test_it_carries_the_values_that_failed(
    connection: asyncpg.Connection, build: Build, swept
):
    # The line that makes the diagnosis a single read rather than an errand.
    text = await rendered(connection, build)

    assert "UAC25022 " in text
    assert "uac25019" in text


async def test_it_says_what_was_declined(connection: asyncpg.Connection, build: Build, swept):
    # "Found no certificates" and "skipped the certificate" are the same empty
    # result with completely different fixes.
    text = await rendered(connection, build)

    assert "DECLINED" in text
    assert "image001.gif" in text


async def test_it_carries_the_trace_including_the_step_that_failed(
    connection: asyncpg.Connection, build: Build, swept
):
    text = await rendered(connection, build)

    assert "TRACE" in text
    assert "extract" in text
    assert "coas_valid" in text


async def test_it_repeats_what_review_settled_about_this_step(
    connection: asyncpg.Connection, build: Build, swept
):
    # Inlined, not referenced. A reader who has to open the spec to find out
    # whether matching is by batch number or page order has left the paste.
    spec = a_spec(with_context=True)

    text = await bundle_.bundle(connection, build=build, spec=spec)

    assert "SPEC CONTEXT" in text
    assert "by its batch number, not by the order the pages arrive" in text
    assert "belongs to a different shipment" in text


async def test_history_names_what_was_already_tried_against_this_signature(
    connection: asyncpg.Connection, build: Build, swept
):
    # Without it the same failed idea gets retried across sessions, which is the
    # specific waste a human-run loop suffers and an autonomous one does not.
    await repairs_repo.save(
        connection,
        Repair(
            build_id=build.id,
            classification="implementation_defect",
            failure_signature="coas_valid :: output_diff :: coa_success",
            summary="trimmed batch numbers before matching",
            status="regressed",
        ),
    )

    text = await rendered(connection, build, signature="coas_valid :: output_diff :: coa_success")

    assert "trimmed batch numbers before matching" in text
    assert "regressed" in text


async def test_history_says_none_rather_than_omitting_the_section(
    connection: asyncpg.Connection, build: Build, swept
):
    # An absent section reads as "I did not check". `none` reads as "nothing has
    # been tried", which is a different and useful fact.
    text = await rendered(connection, build)

    assert re.search(r"REPAIR HISTORY.*\n\s+none", text)


async def test_the_biggest_bucket_is_chosen_when_no_signature_is_named(
    connection: asyncpg.Connection, build: Build, spec_id: UUID, agent_dir: Path
):
    # Which file to open next is a ranking question, and the answer is the
    # bucket covering the most cases.
    cases = [
        await evals_repo.save_case(
            connection,
            spec_id,
            EvalCase(
                key=key,
                input={"produce": {**EXPECTED, **wrong}},
                expected_output=EXPECTED,
            ),
        )
        # Counts have to reconcile — `passed + failed == total` is enforced by
        # CheckResult, so a case built to disagree on one column still has to be
        # arithmetically honest, or it errors instead of mismatching.
        for key, wrong in (
            ("A", {"coa_success": 3, "failed_coa": 2}),
            ("B", {"coa_success": 4, "failed_coa": 1}),
            ("C", {"coa_total": 6, "failed_coa": 1}),
        )
    ]
    await sweep_.sweep(
        connection,
        build=build,
        spec=a_spec(),
        cases=cases,
        agents_root=agent_dir.parent,
        cycle_id=uuid4(),
    )

    text = await rendered(connection, build)

    # `failed_coa` is wrong on all three; `coa_success` on two, `coa_total` on one.
    assert "coas_valid :: output_diff :: failed_coa" in text
    assert "(3 cases)" in text


async def test_a_build_with_nothing_failing_says_so(
    connection: asyncpg.Connection, build: Build, spec_id: UUID, agent_dir: Path
):
    case = await evals_repo.save_case(
        connection,
        spec_id,
        EvalCase(key="CLEAN", input={"produce": EXPECTED}, expected_output=EXPECTED),
    )
    await sweep_.sweep(
        connection,
        build=build,
        spec=a_spec(),
        cases=[case],
        agents_root=agent_dir.parent,
        cycle_id=uuid4(),
    )

    text = await rendered(connection, build)

    assert "3/3" in text
    assert "nothing failing" in text


async def test_a_bundle_of_zeros_says_the_sweep_measured_nothing(
    connection: asyncpg.Connection, build: Build, spec_id: UUID, agent_dir: Path
):
    # The trap: every column expecting zero scores as a pass, so the score can
    # read green while nothing ever reached a check. The emptiness IS the
    # diagnosis, and a bundle that does not say so is nearly empty for no
    # apparent reason.
    zeroed = dict.fromkeys(EXPECTED, 0)
    case = await evals_repo.save_case(
        connection,
        spec_id,
        EvalCase(key="ZERO", input={"produce": zeroed}, expected_output=zeroed),
    )
    await sweep_.sweep(
        connection,
        build=build,
        spec=a_spec(),
        cases=[case],
        agents_root=agent_dir.parent,
        cycle_id=uuid4(),
    )

    text = await rendered(connection, build)

    assert "EMPTY SWEEP" in text
    assert "upstream of everything the eval measures" in text


async def test_nothing_in_the_bundle_is_a_uuid(connection: asyncpg.Connection, build: Build, swept):
    # The property that decides whether the paste works. Every id is a lookup,
    # and a lookup is the thing this exists to remove.
    text = await rendered(connection, build)

    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}", text)


# --- the guesses that produced the code ------------------------------------


def _with_assumptions(agent_dir: Path) -> None:
    """Three assumptions: one about this step, one about another, one unanchored."""
    (agent_dir / "assumptions.json").write_text(
        json.dumps(
            {
                "spec_checksum": a_spec().checksum,
                "assumptions": [
                    {
                        "id": "batch_matching_is_normalised",
                        "decision": "folded case and trimmed whitespace before matching",
                        "prompted_by": "primitives.coas_valid.context.inherited[0]",
                        "because": "the card carries a settled assertion saying so",
                        "falsified_if": "two genuinely different values are treated as one",
                    },
                    {
                        "id": "invoice_pages_are_one_document",
                        "decision": "treated consecutive pages as one instance",
                        "prompted_by": "primitives.invoice_complete.scope",
                        "because": "unrelated to the failing step",
                        "falsified_if": "a document splits",
                    },
                    {
                        "id": "confidence_floor",
                        "decision": "0.6",
                        "prompted_by": None,
                        "because": "nothing in the spec sets one",
                        "falsified_if": "something real is declined",
                    },
                ],
            }
        )
    )


async def test_it_carries_the_assumptions_that_could_explain_this_failure(
    connection: asyncpg.Connection, build: Build, agent_dir: Path, swept
):
    """The bundle answers "which guess predicted this?" without a second lookup.

    Both skills tell a repair agent to read `assumptions.json` first. If the
    block it pastes does not contain them, that instruction only works when
    somebody remembers to open the file — which is exactly the kind of step that
    stops happening.
    """
    _with_assumptions(agent_dir)

    text = await rendered(connection, build, agents_root=agent_dir.parent)

    assert "batch_matching_is_normalised" in text
    assert "two genuinely different values are treated as one" in text


async def test_an_assumption_anchored_nowhere_is_always_carried(
    connection: asyncpg.Connection, build: Build, agent_dir: Path, swept
):
    """A null `prompted_by` is the signal — no anchor in the spec, most likely gap."""
    _with_assumptions(agent_dir)

    text = await rendered(connection, build, agents_root=agent_dir.parent)

    assert "confidence_floor" in text


async def test_assumptions_about_other_steps_are_left_out(
    connection: asyncpg.Connection, build: Build, agent_dir: Path, swept
):
    """The bundle is one signature. Carrying every guess would bury the relevant ones."""
    _with_assumptions(agent_dir)

    text = await rendered(connection, build, agents_root=agent_dir.parent)

    assert "invoice_pages_are_one_document" not in text


async def test_no_assumptions_file_is_not_an_error(
    connection: asyncpg.Connection, build: Build, agent_dir: Path, swept
):
    """A build that recorded none still produces a bundle."""
    text = await rendered(connection, build, agents_root=agent_dir.parent)

    assert "FAILING SIGNATURE" in text


async def test_assumptions_about_the_failing_step_come_first(
    connection: asyncpg.Connection, build: Build, agent_dir: Path, swept
):
    """An unanchored assumption appears in every bundle; an anchored one does not.

    Ordering by the file would put whichever the generator happened to write
    first at the top. On a paste target the first thing read has to be the most
    specific thing known, or the reader skims past it.
    """
    _with_assumptions(agent_dir)

    text = await rendered(connection, build, agents_root=agent_dir.parent)

    assert text.index("batch_matching_is_normalised") < text.index("confidence_floor")
