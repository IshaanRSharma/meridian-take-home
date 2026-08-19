"""The other direction: artifacts a skill wrote, read back into the database.

The bundle is the CLI writing text for a skill to read. **This is a skill
writing files for the CLI to read.** A human in a terminal with a coding agent
produces no rows at all by default — it has no connection string and must not
have one, because that is what keeps the loop runnable by anyone with the repo
and keeps credentials out of it entirely.

So the skill leaves two things behind and the CLI collects them:

    build.json         file_map · entry_point · model · prompt_version · checksum
    a git commit       the patch itself, which is also the diff and the audit trail

**git is the transport for the diff.** `agents/` is committed, so
`source_ref` is `agents/<slug>@<sha>` and every build is recoverable with
`git checkout`. Nothing has to ask the skill what it changed — the commit says.

Which is why an untracked agent directory is refused rather than warned about.
A `source_ref` naming a commit that does not contain the code is worse than no
`source_ref`: it reads as recoverable, and the recovery silently gives you a
different program.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import asyncpg

from meridian import events
from meridian.domain.build import Build, Classification, CreatedBy, Repair
from meridian.domain.errors import ConflictingStateError, NotFoundError
from meridian.domain.frozen import FrozenSpec
from meridian.domain.review import Anchor, Thread
from meridian.healing.gate import Verdict, gate
from meridian.healing.localize import owners_from_spec
from meridian.repositories import builds as builds_repo
from meridian.repositories import evals as evals_repo
from meridian.repositories import repairs as repairs_repo
from meridian.repositories import threads as threads_repo


def _status(verdict: Verdict) -> str:
    """What to call a patch, from what the gate measured — never from its prose.

    The gate can only reject, so `accepted` is not among these: a patch it
    passes stays **proposed** until a person reads the diff. The other two are
    different failures with different next steps — `regressed` means the patch
    broke something that worked, `rejected` means it did not fix what it claimed
    — and reading them off `reason` would make a reworded sentence relabel every
    repair in the table.
    """
    if verdict.accepted:
        return "proposed"
    # Unfixed is checked FIRST, and a patch can be both. A signature still
    # failing is the primary finding — the patch did not do what it claimed —
    # and calling that `regressed` because one unrelated column also moved
    # accuses an approach of breaking something it did not break. On a suite
    # scored by model calls, one column moving between two sweeps of identical
    # code is ordinary; it must not be able to rename a repair.
    if verdict.unfixed:
        return "rejected"
    return "regressed" if verdict.regressed else "rejected"


@dataclass(frozen=True)
class Manifest:
    """`agents/<slug>/build.json`, as the generator wrote it."""

    entry_point: str
    file_map: dict[str, str] = field(default_factory=dict)
    model: str | None = None
    prompt_version: str | None = None
    temperature: float | None = None
    spec_checksum: str | None = None


class NotRegisterableError(ConflictingStateError):
    """The agent on disk cannot honestly be recorded as a build of this spec."""


@dataclass(frozen=True)
class Attempt:
    """One line of `agents/<slug>/repairs/<signature>.jsonl`, as a skill wrote it.

    The skill records an attempt per try; the CLI records a repair per patch.
    Those are the same event written twice, by two parties who cannot see each
    other — the skill has no database and must not have one, and the CLI cannot
    see what a coding agent decided. So the file carries the half only the skill
    knows: **what was tried, and what it concluded** — and neither survives
    anywhere else.
    """

    attempt: int
    signature: str
    tried: str = ""
    outcome: str = ""
    falsified: str | None = None
    note: str = ""
    regressed: tuple[str, ...] = ()

    def line(self) -> str:
        """One attempt as a reader meets it in a bundle.

        `note` last and never truncated: on a `stopped` attempt it carries the
        conclusion that keeps the next session from re-deriving it, which is the
        most expensive sentence in the file to lose.
        """
        parts = [f"  attempt {self.attempt} — {self.outcome or 'unreported'}: {self.tried}"]
        if self.regressed:
            parts.append(f"      regressed {', '.join(self.regressed)}")
        if self.falsified:
            parts.append(f"      falsified {self.falsified}")
        if self.note:
            parts.append(f"      {self.note}")
        return "\n".join(parts)


def attempts_for(agent_dir: Path, signature: str) -> tuple[Attempt, ...]:
    """Every attempt a skill recorded against one signature.

    **Every file in the directory is read, and the signature is taken from
    inside the line rather than from the filename.** The skill is told to name a
    file after the signature, and on the real corpus it did not: a session that
    started on `coa_success` wrote its third attempt against `coa_total` into
    the same file, because that is where the work had led. Trusting the filename
    would have silently dropped exactly the attempt worth keeping — the one that
    changed its mind.

    A malformed line is skipped rather than raised on. The file is appended to
    by a coding agent between runs, so a half-written last line is an ordinary
    state, and losing a repair record over a truncated one would punish the
    party that did the work.
    """
    where = agent_dir / "repairs"
    if not where.is_dir():
        return ()

    found: list[Attempt] = []
    for path in sorted(where.glob("*.jsonl")):
        try:
            body = path.read_text()
        except OSError:
            continue
        for line in body.splitlines():
            entry = _attempt(line, signature)
            if entry is not None:
                found.append(entry)
    return tuple(sorted(found, key=lambda one: one.attempt))


def _attempt(line: str, signature: str) -> Attempt | None:
    if not line.strip():
        return None
    try:
        body = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(body, dict) or body.get("signature") != signature:
        return None
    return Attempt(
        attempt=int(body.get("attempt") or 0),
        signature=signature,
        tried=str(body.get("tried") or ""),
        outcome=str(body.get("outcome") or ""),
        falsified=body.get("falsified") or None,
        note=str(body.get("note") or ""),
        regressed=tuple(str(one) for one in (body.get("regressed") or ())),
    )


def with_attempts(summary: str, attempts: Sequence[Attempt]) -> str:
    """The typed summary, plus what the skill wrote down and nothing else has.

    Folded into `summary` rather than given a column, because the table already
    stores this repair's *outcome* and a migration would only be storing the
    same fact twice at a different grain. What is genuinely absent is the
    reasoning, and reasoning is prose.

    **Attempts already carried by earlier rows are dropped**, counted rather
    than matched: `repair record` is called once per patch and the skill appends
    once per try, so the nth call owns everything after the (n-1)th. Matching on
    the text instead would re-fold every attempt into every row, and the bundle
    prints one summary per repair — so a reader would meet the same refuted
    approach as many times as the loop had turned.
    """
    if not attempts:
        return summary
    lines = [one.line() for one in attempts]
    return f"{summary}\n\nWHAT THE SKILL RECORDED\n" + "\n".join(lines)


def read_manifest(agent_dir: Path) -> Manifest:
    """Read `build.json`, refusing rather than defaulting anything load-bearing.

    `entry_point` has no default because guessing one is how a sweep runs the
    wrong file and reports its results as this build's.
    """
    path = agent_dir / "build.json"
    if not path.is_file():
        raise NotRegisterableError(
            f"{path} does not exist — the generator writes it, and it carries "
            "the file_map every failure bundle resolves a path from"
        )
    try:
        body = json.loads(path.read_text())
    except json.JSONDecodeError as broken:
        raise NotRegisterableError(f"{path} is not valid JSON: {broken}") from broken

    entry = body.get("entry_point")
    if not entry:
        raise NotRegisterableError(f"{path} names no entry_point")
    return Manifest(
        entry_point=str(entry),
        file_map={str(k): str(v) for k, v in (body.get("file_map") or {}).items()},
        model=body.get("model"),
        prompt_version=body.get("prompt_version"),
        temperature=body.get("temperature"),
        spec_checksum=body.get("spec_checksum"),
    )


async def register(  # noqa: PLR0913 - a build names its spec, its code and its author
    connection: asyncpg.Connection,
    *,
    spec: FrozenSpec,
    spec_id: UUID,
    repo_root: Path,
    cycle_id: UUID,
    created_by: CreatedBy = "codegen",
    agents_dir: str = "agents",
) -> Build:
    """Record the agent currently on disk as a build of this spec."""
    agent_dir = repo_root / agents_dir / spec.slug
    manifest = read_manifest(agent_dir)

    if manifest.spec_checksum and manifest.spec_checksum != spec.checksum:
        raise NotRegisterableError(
            f"{agent_dir}/build.json was generated from spec {manifest.spec_checksum[:12]}, "
            f"and this spec is {spec.checksum[:12]} — regenerate, or register against that spec"
        )
    if not (agent_dir / manifest.entry_point).is_file():
        raise NotRegisterableError(
            f"build.json names {manifest.entry_point}, which does not exist under {agent_dir}"
        )
    if not tracked(repo_root, agent_dir):
        raise NotRegisterableError(
            f"{agent_dir} is not tracked by git, so `agents/{spec.slug}@<sha>` would name a "
            "commit that does not contain it — commit the agent, or remove it from .gitignore"
        )

    build = await builds_repo.save(
        connection,
        Build(
            spec_id=spec_id,
            source_ref=f"{agents_dir}/{spec.slug}@{head(repo_root)}",
            created_by=created_by,
            model=manifest.model,
            prompt_version=manifest.prompt_version,
            temperature=manifest.temperature,
            file_map=manifest.file_map,
            entry_point=manifest.entry_point,
        ),
    )
    await events.emit(
        connection,
        cycle_id=cycle_id,
        phase="codegen" if created_by == "codegen" else "repair",
        kind="build",
        status="ok",
        spec_id=spec_id,
        build_id=build.identity,
        detail={
            "iteration": build.iteration,
            "source_ref": build.source_ref,
            "model": build.model,
            "primitives": len(build.file_map),
        },
    )
    return build


async def record_repair(  # noqa: PLR0913 - a repair names what, where, why and against what
    connection: asyncpg.Connection,
    *,
    build: Build,
    signature: str,
    classification: Classification,
    summary: str,
    cycle_id: UUID,
    repo_root: Path,
    spec: FrozenSpec | None = None,
    thread_id: UUID | None = None,
    diff: str | None = None,
) -> tuple[Repair, Verdict | None]:
    """Record one patch, and let the gate decide what to call it.

    `failing_case_ids`, `regressed_case_ids` and `status` are all derived rather
    than supplied. That is the whole point of a gate: it can only reject, a
    human is required to override it, and neither means anything if the numbers
    it judges on were typed in by whoever wrote the patch.

    A `spec_gap` is not gated at all. No patch is correct because no rule
    decides the answer, so there is nothing to measure — it is escalated, and
    the table refuses it without a thread. **If no thread exists yet, one is
    raised here**: the constraint exists to send the question back to the
    process owner, and requiring somebody to go and create a thread first is
    friction on the one path that must never be skipped.

    Whatever the skill wrote into `agents/<slug>/repairs/` is folded into the
    summary on the way past. Until now that file was written and read by nothing
    — the skill is told this command ingests it and it did not — so the one
    thing only the coding agent knew, *what it tried and what it concluded*,
    stopped at the end of the session that learned it.
    """
    parent = await _parent(connection, build)
    verdict = None
    status = "escalated"
    regressed: tuple[UUID, ...] = ()

    already = len(await repairs_repo.history_for(connection, signature, spec_id=build.spec_id))
    summary = with_attempts(
        summary, attempts_for(repo_root / build.directory(), signature)[already:]
    )

    if classification == "spec_gap":
        thread_id = thread_id or await _raise_thread(connection, spec, signature, summary)
    else:
        verdict = await gate(connection, before=parent, after=build, signature=signature)
        status = _status(verdict)
        regressed = await _case_ids(connection, parent, {case for case, _ in verdict.regressed})

    failing = await _failing_case_ids(connection, parent, signature)
    repair = await repairs_repo.save(
        connection,
        Repair(
            build_id=parent.identity,
            classification=classification,
            failure_signature=signature,
            failing_case_ids=failing,
            files_touched=changed(repo_root, parent.commit(), build.commit(), build.directory()),
            summary=summary,
            diff=diff,
            status=status,  # type: ignore[arg-type]
            regressed_case_ids=regressed,
            produced_build_id=build.identity,
            raised_thread_id=thread_id,
        ),
    )
    await events.emit(
        connection,
        cycle_id=cycle_id,
        phase="repair",
        kind="patch",
        status="ok" if status == "proposed" else "rejected",
        build_id=build.identity,
        primitive_key=signature.split(" :: ", 1)[0] if " :: " in signature else None,
        detail={
            "signature": signature,
            "classification": classification,
            "status": status,
            "verdict": verdict.reason if verdict else "spec gap — not gated",
        },
    )
    return repair, verdict


async def unattributable_columns(
    connection: asyncpg.Connection, *, spec: FrozenSpec, spec_id: UUID
) -> tuple[str, ...]:
    """Expected columns no primitive on this board fills.

    The one way a brand-new board silently produces evals worth nothing. A
    column nothing fills is not an error — the sweep scores it, as a permanent
    failure that localises to no file and no repair can ever move — so it never
    announces itself. It just sits at the bottom of every score for ever.

    Worth saying at load time, while the board can still be edited, rather than
    after somebody has generated an agent and spent a cycle wondering which file
    to open. It is the generator's own stop condition — *an eval column nothing
    fills* — asked one step earlier, where the answer is cheaper.
    """
    owners = owners_from_spec(spec)
    measured: set[str] = set()
    for case in await evals_repo.cases_for(connection, spec_id):
        measured |= set(case.expected_output)
    return tuple(sorted(column for column in measured if column not in owners))


async def _raise_thread(
    connection: asyncpg.Connection, spec: FrozenSpec | None, signature: str, summary: str
) -> UUID:
    """Put the question back on the board it came from.

    Anchored on the primitive the signature names, so it lands on the card whose
    behaviour is in doubt rather than on the board at large. `origin='repair'`
    is what separates it in a later round from a question the reviewer asked —
    this one has a failing eval case behind it, which is stronger evidence than
    anything the reviewer had before the freeze.
    """
    if spec is None or spec.board_id is None:
        raise NotRegisterableError(
            "a spec gap has to go back to a board, and this repair names no spec — "
            "pass one, or raise the thread yourself and give it with --thread"
        )

    primitive = signature.split(" :: ", 1)[0] if " :: " in signature else None
    anchors = (
        (Anchor(kind="primitive", key=primitive),)
        if primitive and primitive in spec.primitives
        else ()
    )
    return await threads_repo.save(
        connection,
        spec.board_id,
        Thread(
            category="spec_gap",
            severity="blocking",
            origin="repair",
            question=summary,
            reason=f"raised by the repair loop against {signature}",
            anchors=anchors,
        ),
    )


async def _parent(connection: asyncpg.Connection, build: Build) -> Build:
    if build.parent_build_id is None:
        raise NotRegisterableError(
            f"build {build.iteration} has no parent, so there is nothing to have repaired"
        )
    return await builds_repo.get(connection, build.parent_build_id)


async def _failing_case_ids(
    connection: asyncpg.Connection, parent: Build, signature: str
) -> tuple[UUID, ...]:
    """Which cases this signature was failing on before the patch.

    Read from the parent's sweep rather than taken on trust. A repair that
    claims four cases and fixed one would otherwise be indistinguishable from
    one that fixed four.
    """
    rows = await evals_repo.failures_for(connection, parent.identity, signature=signature)
    return tuple(dict.fromkeys(row["case_id"] for row in rows if row["case_id"]))


async def _case_ids(
    connection: asyncpg.Connection, build: Build, keys: set[str]
) -> tuple[UUID, ...]:
    by_key = {
        r.case_key: r.case_id for r in await evals_repo.results_for(connection, build.identity)
    }
    found = (by_key.get(key) for key in sorted(keys))
    return tuple(case_id for case_id in found if case_id is not None)


# ── git, which is the transport ──────────────────────────────────────────────


def head(repo_root: Path) -> str:
    """The commit the working tree is on."""
    return _git(repo_root, "rev-parse", "--short", "HEAD")


def repo_root(start: Path) -> Path | None:
    """The top of the working tree, asked of git rather than assumed.

    Every path this loop records — `agents/<slug>@<sha>`, `files_touched` — is
    relative to the repository, so deriving them from wherever the shell happens
    to be would make the same command write different rows from different
    directories.

    **No git is no repository, not an error.** The deployed API image carries
    `src/` and `db/` and neither git nor `agents/`, because it serves a database
    and never registers a build — so asking it for a repository root is a
    question with the honest answer "there isn't one". Raising instead turned a
    panel that reads assumptions off disk into a 500 for the whole endpoint,
    taking the scoreboard and the curve down with it. Every caller already
    handles None; the ones that genuinely need a repository, like
    `build register`, refuse on None and say why.
    """
    try:
        found = _git(start, "rev-parse", "--show-toplevel")
    except NotFoundError:
        return None
    return Path(found) if found else None


def tracked(repo_root: Path, path: Path) -> bool:
    """Whether git is carrying this directory at all.

    `ls-files` rather than `check-ignore`: a path can be un-ignored and still
    never have been added, and it is being *in a commit* that makes a
    `source_ref` mean something.
    """
    return bool(_git(repo_root, "ls-files", "--", str(path)))


def changed(repo_root: Path, before: str, after: str, within: str = "") -> tuple[str, ...]:
    """Which of the agent's files moved between two builds.

    **Scoped to the agent's own directory**, and that scope is not cosmetic.
    Two builds are two commits, and anything else committed between them — a
    change to the platform, another agent, the UI — sits in the same range. An
    unscoped diff reported ninety files for a repair that touched one, which
    makes `files_touched` useless for the thing it exists for: telling the next
    reader what this patch actually did.

    Empty when either sha is unknown — a shallow clone, a rebased branch, a
    build registered before the commit existed. An empty list of files is
    honest; a list assembled from something else would not be.
    """
    if not before or not after or before == after:
        return ()
    scope = ["--", within] if within else []
    diff = _git(repo_root, "diff", "--name-only", f"{before}..{after}", *scope)
    return tuple(line for line in diff.splitlines() if line)


def _git(repo_root: Path, *args: str) -> str:
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user string as a command
            ["git", *args],  # noqa: S607 - git resolved from PATH, as every tool here is
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as unavailable:
        raise NotFoundError(f"git is not usable here: {unavailable}") from unavailable
    return done.stdout.strip() if done.returncode == 0 else ""
