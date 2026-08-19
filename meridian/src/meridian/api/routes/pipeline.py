"""Running the agent against mail that has arrived, rather than against answers.

The eval suite asks *was this right*. This asks *what does it say*, about a
shipment nobody has scored — which is the only question production ever gets.
Both go through the same generated workflow: `poll` in the agent's own directory
groups the mailbox and calls the same `run_case` the sweep calls, so this route
cannot drift from the thing that was measured.

**Returns a `cycle_id` and does the work behind it.** A poll fetches a mailbox,
reads scanned documents and drives a workflow per shipment, which is minutes —
long past what a browser will hold open. The caller subscribes to `events` on
the `cycle_id` and watches the shipments land, which is the same path a sweep's
progress already takes.

**A run with no expected row is still recorded.** `runs.case_id` is nullable
precisely for this; the schema's own comment on it reads *null = prod*. What is
stored is the row the process produced plus the trace, so an operator reading it
later has the same evidence a failing eval case would have given them.

**`needs_correlation` is carried in the payload, not in `outcome`.** The column
is constrained to `passed | failed | error | running`, and a pre-alert nobody can
key is none of those — nothing failed, and calling it an error sends somebody to
debug an agent that behaved correctly. So the row is filed as `error` because
that is the only slot that stops it reading as a success, and the actual state is
in the payload where it can say what it means. Widening the enum is a migration
and a decision about the domain, not something a route should do on the way past.
"""

import importlib
import re
from collections.abc import Collection
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from meridian import events
from meridian.api.dependencies import Connection
from meridian.core.db import transaction
from meridian.domain.build import Build
from meridian.domain.errors import NotFoundError
from meridian.domain.primitives import BOARD_KEY
from meridian.healing import record
from meridian.healing.sweep import AgentNotRunnableError, agent_loaded
from meridian.repositories import builds as builds_repo
from meridian.repositories import evals as evals_repo
from meridian.repositories import specs as specs_repo

_IS_CARD_KEY = re.compile(BOARD_KEY)

router = APIRouter(tags=["pipeline"])


class TriggerRequest(BaseModel):
    """Which board's agent to run.

    A body rather than a query parameter, matching the pipeline shape the design
    notes specify — every other pipeline call names its subject the same way, and
    a required identifier in a query string is the one that reads as an option.
    """

    board_id: UUID


class Triggered(BaseModel):
    """What a caller gets back before any of the work has happened."""

    cycle_id: UUID
    build: int
    detail: str


@router.post("/pipeline/trigger", status_code=202)
async def trigger(
    body: TriggerRequest,
    connection: Connection,
    background: BackgroundTasks,
) -> Triggered:
    """Process whatever has arrived that has not been processed before.

    Resolved here and run later, deliberately: a board with no spec, or a spec
    with no registered build, is a mistake the caller can fix and should hear
    about immediately — where the same refusal from a background task would be
    an event nobody was watching for yet.
    """
    built = await _runnable(connection, body.board_id)
    cycle_id = uuid4()
    background.add_task(poll_mailbox, body.board_id, built, cycle_id)
    return Triggered(
        cycle_id=cycle_id,
        build=built.iteration,
        detail=f"polling the mailbox for build {built.iteration}; watch events on this cycle",
    )


async def _runnable(connection: asyncpg.Connection, board_id: UUID) -> Build:
    """The build this board would run, or why there is not one."""
    spec_id = await specs_repo.latest_id(connection, board_id)
    if spec_id is None:
        raise NotFoundError(f"board {board_id} has never been frozen, so there is nothing to run")
    built = await builds_repo.latest(connection, spec_id)
    if built is None:
        raise NotFoundError(
            f"spec for board {board_id} has no registered build — "
            "`meridian build register` reads the agent on disk"
        )
    return built


async def poll_mailbox(board_id: UUID, built: Build, cycle_id: UUID) -> None:
    """One pass of the mailbox, recorded as it goes.

    Its own transaction, because the request's has already been committed and
    returned by the time this runs. Each shipment is recorded as it finishes
    rather than at the end: a poll is minutes long and a watcher with nothing to
    look at cannot tell it from a poll that has wedged.
    """
    async with transaction() as connection, events.during(
        connection,
        cycle_id=cycle_id,
        phase="prod",
        kind="trigger",
        build_id=built.identity,
    ) as detail:
        seen = await _already_processed(connection, built)
        polled = await _poll(built, seen)

        for done in polled.processed:
            await _record_shipment(connection, built, cycle_id, done)
        for gone in polled.uncorrelated:
            await _record_uncorrelated(connection, built, cycle_id, gone)

        detail.update(
            board_id=str(board_id),
            processed=len(polled.processed),
            uncorrelated=len(polled.uncorrelated),
            already_seen=len(polled.already_seen),
        )


async def _poll(built: Build, seen: Collection[str]) -> Any:
    """Load the agent's own trigger and run it.

    The same loader the sweep uses, for the same reason: Temporal re-imports a
    workflow's module by name through the ordinary finders on every workflow
    task, so the agent's directory has to be on `sys.path` for as long as
    anything is running rather than merely long enough to execute a file.

    `agent_loaded` hands back `run_case`; what is wanted here is `poll`, which
    lives beside it and is reachable once the path is set. Importing it inside
    the context is what keeps the two loaders from being two loaders.
    """
    inside = record.repo_root(Path.cwd())
    if inside is None:
        raise NotFoundError("not inside a repository, so `agents/<slug>` has no meaning")
    # `agent_loaded` joins slug and entry point onto what it is given, so it
    # wants the directory agents live IN — not the repository root. Every other
    # caller passes `_repo_root() / "agents"`; passing the root alone resolved
    # to `<repo>/<slug>/src/harness.py`, a path that has never existed, and the
    # trigger died in a background task where nobody was watching for it.
    # Derived from the build, not fixed to `agents/`. `Build.directory()` reads
    # the path out of `source_ref` for the reason its own docstring gives — the
    # spec knows what an agent is CALLED and only the build knows where it was
    # measured. Two implementations of one spec share a slug and cannot sit
    # under one root, so a hardcoded `agents/` runs whichever of them happens to
    # live there and files the result under the other one's build number.
    root = inside / (Path(built.directory()).parent or Path("agents"))

    with agent_loaded(root, built):
        # Resolved by name rather than imported, because the module only exists
        # on `sys.path` inside this block — a static import would be a name the
        # type checker cannot find and, worse, would bind whichever agent
        # happened to be loaded first for the life of the process.
        found = importlib.import_module("trigger")
        poll = getattr(found, "poll", None)
        if poll is None or not callable(poll):
            # Checked here rather than trusted, because the alternative is a
            # TypeError raised inside a BackgroundTasks job — after the caller
            # already has a 202 and a cycle id, and where nothing surfaces it
            # but the server log. `runtime.harness.TriggerPoll` is the shape.
            raise AgentNotRunnableError(
                f"{built.slug()} exposes no callable `poll` — see "
                "meridian.runtime.harness.TriggerPoll for the contract"
            )
        try:
            polled: Any = await poll(seen)
        except TypeError as wrong:
            raise AgentNotRunnableError(
                f"{built.slug()}.poll does not take the contract's arguments: {wrong}. "
                "It is `poll(seen: Collection[str] = ()) -> Polled`; see "
                "meridian.runtime.harness.TriggerPoll"
            ) from wrong
        return polled


async def _already_processed(connection: asyncpg.Connection, built: Build) -> set[str]:
    """Shipments this build has produced a row for already.

    Read from `runs` rather than from a ledger of its own. A second store would
    be a second truth, and the first time one was restored from a backup the two
    would disagree about what had been done.
    """
    rows = await connection.fetch(
        "select output from runs where build_id = $1 and case_id is null",
        built.identity,
    )
    found = set()
    for row in rows:
        payload = evals_repo.as_json(row["output"])
        shipment = payload.get("shipment_no")
        if isinstance(shipment, str):
            found.add(shipment)
    return found


async def _record_shipment(
    connection: asyncpg.Connection, built: Build, cycle_id: UUID, done: Any
) -> None:
    """One processed shipment, with the reasons it might not be trustworthy.

    `outcome` is `passed` only when nothing about the run itself argues against
    the row. There is no expected answer to compare against, so this is never a
    claim that the numbers are right — only that the run reached the checks, the
    checks looked at something, and their arithmetic holds.
    """
    run_id = await evals_repo.save_run(
        connection,
        build_id=built.identity,
        case_id=None,
        mode="prod",
        outcome="passed" if done.trustworthy() else "failed",
        output=dict(done.row) | {"shipment_no": done.shipment},
        # `primitive_key` is the `board_key` domain, so a step called "Check
        # COAs" fails the insert and would cost the whole run. Dropped rather
        # than slugified: a key naming no card is worse than a hole in a trace.
        steps=[
            {k: v for k, v in step.items() if k != "name"} | {"primitive_key": step["name"]}
            for step in done.steps
            if _IS_CARD_KEY.match(str(step.get("name", "")))
        ],
        declined=list(done.declined),
    )
    await events.emit(
        connection,
        cycle_id=cycle_id,
        phase="prod",
        kind="shipment",
        status="ok" if done.trustworthy() else "failed",
        build_id=built.identity,
        run_id=run_id,
        case_key=done.shipment,
        detail={"row": done.row, "concerns": list(done.concerns())},
    )


async def _record_uncorrelated(
    connection: asyncpg.Connection, built: Build, cycle_id: UUID, gone: Any
) -> None:
    """A pre-alert nobody can key, made loud.

    The whole point of the feature. Before this it fell out of
    `group_by_shipment` and the trigger reported a clean pass over a mailbox
    containing a shipment it had ignored — which is the failure mode that looks
    most like success.

    `rejected` rather than `failed` on the event: nothing broke, and the process
    model simply has no rule for this message. It is a question for whoever owns
    the process, and it is filed where they will see it.
    """
    run_id = await evals_repo.save_run(
        connection,
        build_id=built.identity,
        case_id=None,
        mode="prod",
        outcome="error",
        output=gone.as_row(),
        errored=gone.reason,
    )
    await events.emit(
        connection,
        cycle_id=cycle_id,
        phase="prod",
        kind="needs_correlation",
        status="rejected",
        build_id=built.identity,
        run_id=run_id,
        detail=gone.as_row(),
    )
