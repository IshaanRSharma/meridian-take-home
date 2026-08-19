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

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg

from meridian.domain.build import Build, RunResult
from meridian.domain.frozen import FrozenSpec
from meridian.healing.compare import compare, is_empty_sweep, score
from meridian.healing.gym import reachable_columns
from meridian.healing.localize import UNFILLABLE, WRONG, triage
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
    agents_root: Path | None = None,
) -> str:
    """Assemble the failure block for one signature of one build's last sweep.

    With no signature one is chosen, and **by kind rather than by size** — see
    `localize.triage` for why counting cases picks the wrong bucket, and for the
    two ways it demonstrably did on this corpus. Size only breaks ties between
    failures of the same kind.

    A bucket covering *every* case is still evidence, and still worth noticing —
    a per-check bug fails some cases, an infrastructure bug fails all of them the
    same way. That signal now lives inside a rank rather than deciding the rank.
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

    lines += await _prior(connection, build.spec_id)

    chosen, rank = _choose(found, reachable_columns(spec), asked=signature)
    bucket = [row for row in found if row["signature"] == chosen]
    if rank == UNFILLABLE:
        # Never handed to a repair agent as work. A patch that produced this
        # column would be inventing a business rule nobody approved, and it
        # would pass the suite while breaking conformance — the one shape of
        # "green" this loop must never reward.
        lines += _unfillable(bucket, chosen)
        return "\n".join(lines) + "\n"

    lines += await _bucket(connection, bucket, chosen, results, spec, build, agents_root)
    return "\n".join(lines) + "\n"


def _choose(
    found: Sequence[dict[str, Any]], reachable: frozenset[str], *, asked: str | None
) -> tuple[str, int]:
    """Which signature to work on, and what kind of failure it is.

    `found` arrives ordered by bucket size, so taking the first of the best rank
    keeps size as the tie-break without sorting for it again — and keeps the
    order stable, which matters because a paste target that moves between
    identical runs is a paste target nobody trusts.

    An explicitly asked-for signature is never re-ranked away from — somebody
    naming one has a reason, and overriding them would make the flag useless in
    exactly the case it exists for. Its *kind* is still reported, so asking for
    an unfillable column gets the spec-gap block rather than a repair bundle
    with no file in it. That is honouring the request, not refusing it: the
    answer to "show me this one" is what this one actually is.
    """
    ranked = [(triage(str(row["detector"]), row["detail"], reachable), row) for row in found]
    if asked is not None:
        best = next((rank for rank, _ in ranked), WRONG)
        return asked, best
    rank, row = min(ranked, key=lambda pair: pair[0])
    return str(row["signature"]), rank


def _unfillable(bucket: Sequence[dict[str, Any]], signature: str) -> list[str]:
    """The bundle for a failure that is nobody's patch to write.

    Not an empty bundle and not an apology: the finding *is* that the board is
    missing a card, and the block says so in the same shape a repair bundle
    takes so a reader does not have to notice they are in a different mode. It
    names the command, because the escalation raises the thread itself and the
    one path that must never be skipped should not also require somebody to
    remember its arguments.
    """
    cases = ", ".join(str(row["case_key"]) for row in bucket)
    column = bucket[0]["detail"].get("column", signature)
    return [
        f"NOT A REPAIR  {signature}   ({len(bucket)} case(s))",
        "",
        f"  No card on this board fills `{column}`, so the sweep scores it as a",
        "  permanent failure that localises to no file. No patch can move it, and",
        "  code that produced it would be inventing a rule nobody approved.",
        "",
        f"  FAILING   {cases}",
        "",
        "  This is a spec gap. Send it back to the board:",
        "",
        '    meridian repair record <board> --class spec_gap \\',
        f'      --signature "{signature}" \\',
        '      --summary "<what the column counts, and what nobody has said>"',
        "",
        "  The escalation raises the thread itself, anchored on the card the",
        "  signature names. No --thread is needed.",
        "",
    ]


async def _bucket(  # noqa: PLR0913, PLR0917 - one section, and it needs the whole picture
    connection: asyncpg.Connection,
    bucket: Sequence[dict[str, Any]],
    signature: str,
    results: Sequence[RunResult],
    spec: FrozenSpec,
    build: Build,
    agents_root: Path | None,
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
    lines += _hints(agents_root, build, primitive)
    lines += _assumptions(agents_root, build, primitive)
    lines += await _history(connection, signature, build.spec_id)
    return lines


def _hints(agents_root: Path | None, build: Build, primitive: str | None) -> list[str]:
    """What a person told this loop between iterations.

    The one place human context can enter a repair without touching the spec.
    An engineer who works something out while reading a failure has nowhere
    else to put it: `spec.lock.json` is checksummed and approved by somebody
    else, and the code is the thing being judged. A hint is neither — it is
    implementation guidance, owned by whoever is repairing, and deliberately
    outside the checksum so writing one does not drift the build from the
    contract it claims to implement.

    **A hint is not an answer to a business question.** If a person had to
    decide something two competent people could disagree about, that belongs on
    a thread and comes back as an assertion in the spec. The test is the same
    one the classifier uses, and putting a business decision here would launder
    it into code with nobody's approval on it.

    Keyed by primitive, with `*` for anything that applies everywhere.
    """
    if agents_root is None:
        return []
    try:
        body = json.loads((agents_root / build.slug() / "hints.json").read_text())
    except (OSError, json.JSONDecodeError):
        return []

    written = [*body.get("*", []), *(body.get(primitive, []) if primitive else [])]
    if not written:
        return []

    return ["HINTS SOMEBODY LEFT FOR THIS", *(f"  {hint}" for hint in written), ""]


def _assumptions(agents_root: Path | None, build: Build, primitive: str | None) -> list[str]:
    """The guesses that could explain this failure, from the build that made them.

    `file_map` resolves a failing column to a file. Nothing resolved it to the
    *decision* behind that file, so "which assumption predicted this?" — the
    first question both skills tell a repair agent to ask — could only be
    answered by remembering to open a second file. This carries the answer.

    Two are relevant and the rest are noise: those anchored on the failing step,
    and those anchored nowhere at all. A null `prompted_by` means the generator
    chose something the spec never mentioned, which is the likeliest gap on the
    board and is worth seeing whatever failed.
    """
    if agents_root is None:
        return []
    try:
        body = json.loads((agents_root / build.slug() / "assumptions.json").read_text())
    except (OSError, json.JSONDecodeError):
        # A build that recorded none is a worse build, not a broken one.
        return []

    anchored, unanchored = [], []
    for entry in body.get("assumptions") or []:
        if primitive and primitive in str(entry.get("prompted_by") or ""):
            anchored.append(entry)
        elif entry.get("prompted_by") is None:
            unanchored.append(entry)

    # Anchored first, and it matters: an assumption about the step that failed
    # is a candidate cause, while an unanchored one is a candidate cause of
    # anything and appears in every bundle. Ordering by the file would put
    # whichever the generator happened to write first at the top, which is the
    # one thing about a paste target that must not be arbitrary.
    relevant = anchored + unanchored
    if not relevant:
        return []

    lines = ["ASSUMPTIONS THAT COULD EXPLAIN THIS"]
    if anchored and unanchored:
        lines.append(f"  ({len(anchored)} about this step, then {len(unanchored)} unanchored)")
    for entry in relevant:
        anchor = entry.get("prompted_by") or "nothing in the spec"
        lines.append(f"  [{entry.get('id')}] {entry.get('decision')}")
        lines.append(f"       because   {entry.get('because')}")
        lines.append(f"       prompted  {anchor}")
        lines.append(f"       wrong if  {entry.get('falsified_if')}")
    lines.append("")
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
            # `failing` is written by two different producers and they disagree
            # in shape. A Check's own result carries whole `Failure` records;
            # `apply_fills` projects the SAME key into the output row as a list
            # of bare subjects, because an email naming the batches wants names
            # and not evidence rows. A bundle that assumed either one crashes on
            # the other — and the bundle is the deliverable, so it renders both.
            if isinstance(failure, str):
                lines.append(f"     {step['primitive_key']}: {failure!r}")
                continue
            if not isinstance(failure, dict):
                continue
            available = (failure.get("detail") or {}).get("available")
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


async def _prior(connection: asyncpg.Connection, spec_id: UUID | None) -> list[str]:
    """What every earlier attempt against this spec adds up to.

    `REPAIR HISTORY` answers *has this exact signature been tried* and nothing
    answers the question a reader actually opens with: **where do the bugs in
    this corpus live?** Those are different, and the second is the one that
    stops a session re-deriving a shape somebody already found. On this repo the
    answer was lopsided — reading and extraction produced almost every accepted
    fix and check logic produced a regression — and a reader who knows that
    opens the reader first, which is exactly where the next bug was.

    Grouped by the primitive a signature names, because that is the coarsest
    grouping that still says *which part of the agent*, and it is free: it is
    the leading segment of a string already stored.

    **Refuted attempts are reproduced in full and never summarised.** An
    approach recorded as regressed or stopped is the single most expensive thing
    to rediscover, and a count of them tells a reader nothing about which
    approach to avoid.

    Read here rather than through a repository function because there is no
    `for_spec` on the repairs repository and adding one is outside this change;
    `gym.episode` reads the same table the same way for the same reason.
    """
    if spec_id is None:
        return []
    rows = await connection.fetch(
        "select r.failure_signature, r.status, r.summary from repairs r "
        "join agent_builds b on b.id = r.build_id where b.spec_id = $1 "
        "order by r.created_at",
        spec_id,
    )
    if not rows:
        return []

    tally: dict[str, dict[str, int]] = {}
    refuted: list[tuple[str, str]] = []
    for row in rows:
        signature = str(row["failure_signature"])
        where = signature.split(" :: ", 1)[0] if " :: " in signature else signature
        counted = tally.setdefault(where, {"proposed": 0, "regressed": 0, "escalated": 0})
        status = str(row["status"])
        counted[status] = counted.get(status, 0) + 1
        if status in {"regressed", "rejected"}:
            refuted.append((signature, str(row["summary"])))

    lines = ["WHAT THIS CORPUS HAS ALREADY TAUGHT THE LOOP"]
    for where, counted in sorted(tally.items(), key=lambda pair: -sum(pair[1].values())):
        said = ", ".join(f"{n} {name}" for name, n in counted.items() if n)
        lines.append(f"  {where:<28} {said}")
    for signature, summary in refuted:
        lines.append(f"  REFUTED — do not retry  [{signature}]")
        for wrapped in summary.splitlines():
            lines.append(f"      {wrapped}")
    lines.append("")
    return lines


async def _history(connection: asyncpg.Connection, signature: str, spec_id: UUID) -> list[str]:
    """Everything tried against this signature, for THIS spec.

    Across builds, because a signature that survived three builds has three
    attempts behind it. Not across specs: the same string on another board is a
    different question about different code, and answering it here would tell a
    reader that something has been tried when nothing has.
    """
    tried = await repairs_repo.history_for(connection, signature, spec_id=spec_id)
    lines = ["REPAIR HISTORY FOR THIS SIGNATURE"]
    if not tried:
        return [*lines, "  none"]

    for repair in tried:
        lines.append(f"  [{repair.status}] {repair.summary}")
        if repair.files_touched:
            lines.append(f"           touched {', '.join(repair.files_touched)}")
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
