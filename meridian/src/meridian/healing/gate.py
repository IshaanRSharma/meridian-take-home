"""Target passes AND nothing that passed before now fails.

The only automatic decision in the pipeline, and it **can only reject**. A human
is required to override it and never to approve in its place, which is what
makes an override a deliberate act rather than a rubber stamp.

Both halves are per column, and the second half is the reason. A patch that
fixes `coa_success` and breaks `failed_coa` leaves the case failing before and
failing after — so a gate comparing cases sees nothing happen, and a real
regression goes through looking like no change at all.

It reads two stored sweeps rather than running one. Both builds have already
been measured, so "nothing that passed before now fails" costs a query — and a
gate that re-ran the parent build would be measuring code that has since been
edited, which is a different question with the same shape.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from meridian.domain.build import Build
from meridian.healing.compare import compare
from meridian.repositories import evals as evals_repo


@dataclass(frozen=True)
class Verdict:
    """What the gate made of a patch, and why.

    `reason` is written for a person deciding whether to override, so it names
    the case and the column rather than counting them.
    """

    accepted: bool
    fixed: tuple[str, ...] = ()
    regressed: tuple[tuple[str, str], ...] = ()
    unfixed: tuple[str, ...] = ()
    """Cases where the signature this patch was aimed at still fails.

    Carried separately from `regressed` because they are different failures with
    different next steps, and a patch can be both at once. Reading the status off
    `regressed` alone filed a patch that simply had not finished the job as one
    that BROKE something — which is the more alarming of the two and the wrong
    thing to leave in a repair history the next session reads as fact.
    """

    reason: str = ""


async def gate(
    connection: asyncpg.Connection, *, before: Build, after: Build, signature: str
) -> Verdict:
    """Judge one patch by the two sweeps that bracket it."""
    was = await _columns(connection, before)
    now = await _columns(connection, after)

    if not now:
        # An unswept build has no failing signature, which reads as "the target
        # passes", and no columns, which reads as "nothing regressed". Both are
        # true of a build nobody measured, and together they accept anything.
        return Verdict(accepted=False, reason=f"no sweep on build {after.iteration} to judge")

    # Working case by case is the right way to run this loop, and it is only
    # safe while the set grows. A case dropped from the later sweep is absent
    # rather than failing, and absent reads as "did not regress" — so a patch
    # measured on one case would clear a baseline of nine. Refuse rather than
    # rely on whoever ran it having remembered.
    dropped = tuple(
        sorted({case for (case, _), agreed in was.items() if agreed} - {case for case, _ in now})
    )
    if dropped:
        return Verdict(
            accepted=False,
            reason=(
                f"build {after.iteration} was measured on fewer cases than build "
                f"{before.iteration}: {', '.join(dropped)} passed before and did not run. "
                "Sweep at least what the earlier build swept."
            ),
        )

    still_failing = await evals_repo.failures_for(connection, after.identity, signature=signature)
    # Only a column that PASSED before and fails now. Counting one that was
    # already broken would make every partial fix look like a regression, and
    # nothing would ever get through.
    regressed = tuple(
        sorted(key for key, agreed in was.items() if agreed and now.get(key) is False)
    )
    return _verdict(signature, still_failing, regressed, _fixed(was, now, signature))


def _fixed(
    was: dict[tuple[str, str], bool],
    now: dict[tuple[str, str], bool],
    signature: str,
) -> tuple[str, ...]:
    """Cases where the column this signature names has flipped to passing.

    A signature ends in its own column — `… :: output_diff :: coa_success` — so
    what the patch fixed is exactly where that column changed its mind.
    """
    column = signature.rsplit(" :: ", 1)[-1]
    return tuple(
        sorted(
            case
            for (case, named), agreed in now.items()
            if named == column and agreed and was.get((case, named)) is False
        )
    )


def _verdict(
    signature: str,
    still_failing: tuple[dict[str, object], ...],
    regressed: tuple[tuple[str, str], ...],
    fixed: tuple[str, ...],
) -> Verdict:
    if still_failing:
        unfixed = tuple(sorted({str(row["case_key"]) for row in still_failing}))
        return Verdict(
            accepted=False,
            fixed=fixed,
            regressed=regressed,
            unfixed=unfixed,
            reason=f"{signature} is still failing on {', '.join(unfixed)}",
        )
    if regressed:
        broke = ", ".join(f"{case}.{column}" for case, column in regressed)
        return Verdict(
            accepted=False,
            fixed=fixed,
            regressed=regressed,
            reason=f"passed before and fails now: {broke}",
        )
    return Verdict(
        accepted=True,
        fixed=fixed,
        reason=f"{signature} passes and nothing that passed before now fails",
    )


async def _columns(connection: asyncpg.Connection, build: Build) -> dict[tuple[str, str], bool]:
    """Every `(case, column)` this build measured, and whether it agreed.

    A case that errored contributes every column it was measured on, all
    failing. That is what makes "a patch that made a working case raise" rank
    above "a patch that miscounted one column", which is the correct ordering.
    """
    measured: dict[tuple[str, str], bool] = {}
    for result in await evals_repo.results_for(connection, build.identity):
        compared = compare(
            result.case_key, result.expected_output, result.output, errored=result.errored
        )
        for column in compared.matched:
            measured[result.case_key, column] = True
        for mismatch in compared.mismatched:
            measured[result.case_key, mismatch.column] = False
    return measured
