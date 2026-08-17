"""The block somebody pastes. This is the product.

Everything else in this package is a terminal: a sweep is a loop, a gate is a
comparison, a repair is a row. What the platform actually sells is that a
failure becomes **legible enough that one paste fixes it** — and the test for
that is brutal and simple:

> Nothing in this block may require a further lookup.

No run id, no case id, no "see the spec for context". A reader who has to
resolve an identifier has left the paste, and once they are back in a database
they may as well have read the failure themselves. So the file path is spelled
out, the disagreeing values are printed, the values that failed are printed, the
settled knowledge about the step is repeated inline, and what has already been
tried against this signature is listed rather than cited.

Two of the sections are here because half of a failure is invisible without
them. `DECLINED` separates "found no certificates" from "skipped the
certificate", which are the same empty result with entirely different fixes.
`REPAIR HISTORY` stops the same failed idea being retried across sessions —
the specific waste a human-run loop suffers and an autonomous one does not.

And a sweep of zeros gets its own header, because the emptiness *is* the
diagnosis: nothing reached a check, so nothing could disagree, and the bundle
being nearly empty is the finding rather than a fault in the bundle.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import asyncpg

from meridian.domain.build import Build, RunResult
from meridian.domain.frozen import FrozenSpec
from meridian.healing.compare import compare, is_empty_sweep, score
from meridian.repositories import evals as evals_repo
from meridian.repositories import repairs as repairs_repo

RULE = "─" * 62
CASES_SHOWN = 3
"""How many cases of a bucket to spell out in full.

Enough to show whether the failures share a shape; few enough that the reader
still reaches SPEC CONTEXT. The rest are named on one line so nothing is hidden.
"""


async def bundle(
    connection: asyncpg.Connection,
    *,
    build: Build,
    spec: FrozenSpec,
    signature: str | None = None,
) -> str:
    """Assemble the failure block for one signature of one build's last sweep.

    With no signature the largest bucket is chosen: "which file do I open next"
    is a ranking question, and the answer is the bucket covering the most cases.
    A bucket covering *every* case is itself evidence — a per-check bug fails
    some cases, an infrastructure bug fails all of them the same way.
    """
    results = await evals_repo.results_for(connection, build.identity)
    compared = [
        compare(r.case_key, r.expected_output, r.output, errored=r.errored) for r in results
    ]
    passed, total = score(compared)

    lines = [f"BUILD {build.iteration} · sweep · {passed}/{total}", ""]
    if is_empty_sweep(compared) and compared:
        lines += _empty_sweep(results)

    found = await evals_repo.failures_for(connection, build.identity, signature=signature)
    if not found:
        lines.append("nothing failing" if compared else "no sweep on this build yet")
        return "\n".join(lines) + "\n"

    bucket = [row for row in found if row["signature"] == found[0]["signature"]]
    chosen = str(found[0]["signature"])
    lines += await _bucket(connection, bucket, chosen, results, spec, build)
    return "\n".join(lines) + "\n"


async def _bucket(  # noqa: PLR0913, PLR0917 - one section, and it needs the whole picture
    connection: asyncpg.Connection,
    bucket: Sequence[dict[str, Any]],
    signature: str,
    results: Sequence[RunResult],
    spec: FrozenSpec,
    build: Build,
) -> list[str]:
    plural = "case" if len(bucket) == 1 else "cases"
    primitive = bucket[0]["primitive_key"]
    where = bucket[0]["detail"].get("file")

    lines = [f"FAILING SIGNATURE  {signature}   ({len(bucket)} {plural})", ""]
    lines.append(f"FILE     {where or _no_file(bucket[0], build)}")
    if primitive:
        lines.append(f"SPEC     spec.lock.json § primitives.{primitive}")
    lines.append("")

    run_ids = [row["run_id"] for row in bucket]
    trajectories = await evals_repo.trajectories_for(connection, run_ids)
    declines = await evals_repo.declines_for(connection, run_ids)
    by_key = {r.case_key: r for r in results}

    for row in bucket[:CASES_SHOWN]:
        lines += _case(row, by_key.get(str(row["case_key"])), trajectories, declines)
    if len(bucket) > CASES_SHOWN:
        rest = ", ".join(str(row["case_key"]) for row in bucket[CASES_SHOWN:])
        lines += [f"ALSO FAILING  {rest}", ""]

    lines += _context(spec, primitive)
    lines += await _history(connection, signature, spec)
    return lines


def _case(
    row: dict[str, Any],
    result: RunResult | None,
    trajectories: dict[UUID, list[dict[str, Any]]],
    declines: dict[UUID, list[dict[str, Any]]],
) -> list[str]:
    lines = [f"CASE     {row['case_key']}"]

    if result and result.errored:
        lines.append(f"  ERRORED        {result.errored}")
    elif result:
        # Only the columns that moved. Printing all seven would bury the two
        # that did, and the ones that agreed have nothing to contribute.
        wrong = compare(result.case_key, result.expected_output, result.output)
        for mismatch in wrong.mismatched:
            lines.append(
                f"  {mismatch.column:<22} expected {mismatch.expected!s:>5}"
                f"   actual {mismatch.actual!s:>5}"
            )
    lines.append("")

    steps = trajectories.get(row["run_id"], [])
    if steps:
        lines.append("TRACE")
        lines += [_step(step) for step in steps]
        lines += _evidence(steps)
        lines.append("")

    skipped = declines.get(row["run_id"], [])
    if skipped:
        lines.append(f"DECLINED  ({len(skipped)})")
        lines += [f"  {gone.get('source', '?'):<24} {gone.get('reason', '')}" for gone in skipped]
        lines.append("")
    return lines


def _step(step: dict[str, Any]) -> str:
    status = {"ok": "ok", "failed": "FAIL", "skipped": "skip"}.get(step["status"], step["status"])
    summary = step.get("error") or _counts(step.get("output") or {})
    return f"  step {step['seq']:<2} {step['primitive_key']:<28} {status:<5} {summary}"


def _counts(output: dict[str, Any]) -> str:
    if {"total", "passed", "failed"} <= output.keys():
        return (
            f"{output.get('outcome', '')}  "
            f"{output['passed']}/{output['total']} passed, {output['failed']} failed"
        )
    return ", ".join(f"{k}: {v}" for k, v in output.items() if k != "failing")[:70]


def _evidence(steps: Sequence[dict[str, Any]]) -> list[str]:
    """The values that failed, as the process owner would name them.

    The single most useful thing in the block. `['UAC25022 ', 'uac25019']` shows
    trailing whitespace and case variance without anybody explaining it, and a
    reader solves it in one look — where "2 unmatched" sends them to the source
    documents.
    """
    lines = []
    for step in steps:
        for failure in (step.get("output") or {}).get("failing") or []:
            available = failure.get("detail", {}).get("available")
            against = f"   against {available}" if available else ""
            lines.append(
                f"     {step['primitive_key']}: {failure.get('subject')!r}"
                f" — {failure.get('reason')}{against}"
            )
    return ["", "  EVIDENCE", *lines] if lines else []


def _context(spec: FrozenSpec, primitive: str | None) -> list[str]:
    """What review settled about this step, inlined rather than referenced.

    A reader who has to open `spec.lock.json` to find out whether matching is by
    batch number or by page order has left the paste, which is the one thing
    this block exists to prevent.
    """
    card = spec.primitives.get(primitive or "")
    if card is None or card.context.is_empty():
        return []

    lines = [f"SPEC CONTEXT FOR {primitive}"]
    lines += [f"  {line}" for line in card.context.inherited]
    lines += [f"  {line}" for line in card.context.local]
    # Never overridden, and stated as a prohibition rather than as a rule: it is
    # a statement about the world, not about this step.
    lines += [f"  [never] {line}" for line in card.context.negative]
    return [*lines, ""]


async def _history(connection: asyncpg.Connection, signature: str, spec: FrozenSpec) -> list[str]:
    tried = await repairs_repo.history_for(connection, signature)
    lines = ["REPAIR HISTORY FOR THIS SIGNATURE"]
    if not tried:
        return [*lines, "  none"]

    for repair in tried:
        lines.append(f"  [{repair.status}] {repair.summary}")
        if repair.files_touched:
            lines.append(f"           touched {', '.join(repair.files_touched)}")
    _ = spec
    return lines


def _empty_sweep(results: Sequence[RunResult]) -> list[str]:
    return [
        "EMPTY SWEEP — every case counted nothing.",
        "",
        f"  {len(results)} case(s) ran and none produced a non-zero value, so every column",
        "  whose expected value happens to be zero scored as a pass. The failure is",
        "  upstream of everything the eval measures: nothing reached a check, so",
        "  nothing could disagree with the expected output.",
        "",
        "  Work FORWARDS from the entry point rather than backwards from a failure —",
        "  read DECLINED and the trace below, not the file a signature names.",
        "",
    ]


def _no_file(row: dict[str, Any], build: Build) -> str:
    """Why there is no path, rather than a path that might be wrong."""
    note = row["detail"].get("note")
    if note:
        return f"unknown — {note}"
    if row["detail"].get("candidates"):
        return f"unknown — {', '.join(row['detail']['candidates'])} both fill this column"
    return f"unknown — no primitive in build {build.iteration} fills this column"
