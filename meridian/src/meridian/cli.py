"""The control surface.

Not a wrapper around the system — the same functions the tests call, reached
from a terminal. That is the property worth protecting: everything demonstrable
here is demonstrable headlessly, and later through HTTP, because none of the
work lives in this file. What lives here is rendering and exit codes.

Every command opens one transaction and closes it. A round of review writes
threads, scenarios and statements together or writes none of them, which is what
makes a round a transaction over a board rather than a sequence of edits that
can half-happen.

Errors are rendered, never re-derived. `BoardNotReadyError` carries its findings
and its unsettled threads precisely so that two callers can present the same
refusal without either of them reconstructing it from prose — this prints them,
and the API returns them as 422.

Schema and seed data are deliberately absent: `make migrate` and `make db` own
those, and a second way to do the same thing is a second way to do it wrong.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated
from uuid import UUID

import asyncpg
import typer

from meridian.compiler import freeze as freeze_
from meridian.compiler import rules
from meridian.core.db import close_pool, transaction
from meridian.domain.errors import ConflictingStateError, IncompleteError, NotFoundError
from meridian.domain.graph import BoardFinding
from meridian.domain.review import Assertion, Thread
from meridian.repositories import assertions as assertions_repo
from meridian.repositories import boards, specs
from meridian.repositories import threads as threads_repo
from meridian.reviewer import run as review_
from meridian.reviewer.distill import paraphrases

app = typer.Typer(
    name="meridian",
    help="Turn tacit process knowledge into a running agent.",
    no_args_is_help=True,
    add_completion=False,
)
board_app = typer.Typer(help="A board, as it is being drawn.", no_args_is_help=True)
review_app = typer.Typer(help="Ask what the drawing does not say.", no_args_is_help=True)
thread_app = typer.Typer(help="Conversations about a board.", no_args_is_help=True)
spec_app = typer.Typer(help="The frozen contract a code generator reads.", no_args_is_help=True)

app.add_typer(board_app, name="board")
app.add_typer(review_app, name="review")
app.add_typer(thread_app, name="thread")
app.add_typer(spec_app, name="spec")

BoardId = Annotated[UUID, typer.Argument(help="the board's id")]
ThreadId = Annotated[UUID, typer.Argument(help="the conversation's id")]


def _run[T](work: Callable[[asyncpg.Connection], Awaitable[T]]) -> T:
    """One command, one transaction, and a readable failure.

    Typer commands are synchronous, so the async boundary is crossed exactly
    once, here. Domain errors are rendered rather than raised: a stack trace
    tells the person running this nothing they can act on, and every one of
    these errors already carries what they need.
    """

    async def go() -> T:
        try:
            async with transaction() as connection:
                return await work(connection)
        finally:
            await close_pool()

    try:
        return asyncio.run(go())
    except freeze_.BoardNotReadyError as refused:
        _findings(refused.findings)
        for thread in refused.unsettled:
            typer.echo(f"  unsettled  {thread.question}")
        raise typer.Exit(1) from refused
    except review_.NotReadyForReviewError as refused:
        typer.echo("This is not a process yet:")
        _findings(refused.findings)
        raise typer.Exit(1) from refused
    except IncompleteError as refused:
        typer.echo(str(refused))
        raise typer.Exit(1) from refused
    except NotFoundError as missing:
        typer.echo(f"Not found: {missing}")
        raise typer.Exit(1) from missing
    except ConflictingStateError as conflict:
        typer.echo(str(conflict))
        raise typer.Exit(1) from conflict
    except (OSError, asyncpg.PostgresError) as unreachable:
        typer.echo(f"Cannot reach the database: {unreachable}")
        raise typer.Exit(2) from unreachable


# ── the board ────────────────────────────────────────────────────────────────


@board_app.command("lint")
def board_lint(board_id: BoardId) -> None:
    """What is missing, worst first.

    Exits non-zero only on `blocking`, because that is the gate: blocking means
    the drawing does not work as a process. A missing value is a question for
    the review, never a refusal handed to someone who came to draw a diagram.
    """
    board = _run(lambda c: boards.get(c, board_id))
    found = rules.findings(board)

    if not found:
        typer.echo("nothing missing")
        return
    _findings(found)
    if any(finding.severity == "blocking" for finding in found):
        raise typer.Exit(1)


# ── review ───────────────────────────────────────────────────────────────────


@review_app.command("run")
def review_run(board_id: BoardId) -> None:
    """Ask what is still worth asking about this board."""
    result = _run(lambda c: review_.review(c, board_id))

    typer.echo(
        f"round {result.number}  ·  {len(result.asked)} asked  ·  "
        f"{len(result.resolved)} resolved  ·  {len(result.reopened)} reopened"
    )
    if result.consulted:
        typer.echo(f"looked at: {', '.join(f'{k} x{v}' for k, v in result.consulted.items())}")
    if result.dropped:
        typer.echo(f"dropped: {result.dropped}")
    typer.echo("")
    for thread in result.asked:
        _thread(thread)


@review_app.command("settle")
def review_settle(board_id: BoardId) -> None:
    """Turn finished conversations into statements, and close what is proved.

    The freeze path runs this too. A review that ends with the last question
    answered has no next round to distil it, so without this the spec would be
    sealed missing exactly the knowledge the final round produced.
    """
    resolved = _run(lambda c: review_.settle(c, board_id))
    settled = _run(lambda c: assertions_repo.for_board(c, board_id))

    typer.echo(f"{len(settled)} statement(s) settled, {len(resolved)} question(s) resolved")
    for statement in settled:
        typer.echo(f"  {statement.anchor!s:<46} [{statement.kind}] {statement.statement}")


# ── conversations ────────────────────────────────────────────────────────────


@thread_app.command("list")
def thread_list(
    board_id: BoardId,
    status: Annotated[
        str | None, typer.Option(help="open · answered · rejected · resolved")
    ] = None,
) -> None:
    """Every question asked about this board, and where each one got to."""
    found = _run(lambda c: threads_repo.for_board(c, board_id))
    shown = [thread for thread in found if status is None or thread.status == status]

    for thread in shown:
        _thread(thread, with_id=True)
    typer.echo(f"{len(shown)} of {len(found)}")


@thread_app.command("answer")
def thread_answer(
    thread_id: ThreadId,
    body: Annotated[str, typer.Argument(help="the answer, in your own words")],
) -> None:
    """Answer a question. That the drawing then shows it is a separate matter."""

    async def work(connection: asyncpg.Connection) -> None:
        await threads_repo.add_message(connection, thread_id, "human", body)
        await threads_repo.set_status(connection, thread_id, "answered")

    _run(work)
    typer.echo("answered — run `meridian review settle` to distil it")


@thread_app.command("reject")
def thread_reject(
    thread_id: ThreadId,
    body: Annotated[str | None, typer.Argument(help="why it does not apply")] = None,
) -> None:
    """Dismiss a question. Still knowledge: it reaches the spec as what is *not* done."""

    async def work(connection: asyncpg.Connection) -> None:
        if body:
            await threads_repo.add_message(connection, thread_id, "human", body)
        await threads_repo.set_status(connection, thread_id, "rejected")

    _run(work)
    typer.echo("dismissed — it will not be asked again")


# ── the spec ─────────────────────────────────────────────────────────────────


@spec_app.command("freeze")
def spec_freeze(board_id: BoardId) -> None:
    """Seal the board into the contract a code generator reads.

    Settles first. Anything answered since the last round becomes a statement
    here or never does.
    """

    async def work(connection: asyncpg.Connection) -> tuple[int, str]:
        await review_.settle(connection, board_id)
        board = await boards.get(connection, board_id)
        sealed = freeze_.freeze(
            board,
            await assertions_repo.for_board(connection, board_id),
            await threads_repo.for_board(connection, board_id),
            previous=await specs.latest(connection, board_id),
        )
        await specs.save(connection, sealed)
        await boards.mark_submitted(connection, board_id)
        return sealed.version, sealed.checksum

    version, checksum = _run(work)
    typer.echo(f"frozen  v{version}  ·  {checksum[:12]}")


@spec_app.command("show")
def spec_show(board_id: BoardId) -> None:
    """Every card with what was settled about it, which is what codegen reads."""
    sealed = _run(lambda c: specs.latest(c, board_id))
    if sealed is None:
        typer.echo("nothing frozen yet")
        raise typer.Exit(1)

    typer.echo(f"v{sealed.version}  ·  {sealed.checksum[:12]}  ·  frozen {sealed.frozen_at}")
    for key, card in sealed.primitives.items():
        typer.echo(f"\n  {key}  ({card.primitive_type})")
        for line in card.context.inherited:
            typer.echo(f"     inherited  {line}")
        for line in card.context.local:
            typer.echo(f"     local      {line}")
        for line in card.context.negative:
            typer.echo(f"     negative   {line}")


@spec_app.command("yield")
def spec_yield(board_id: BoardId) -> None:
    """How much of what was settled the drawing could not have said itself.

    A statement that only restates the graph is correctly anchored, correctly
    inlined, correctly checksummed and worth nothing. This is the number that
    tells them apart.
    """

    async def work(
        connection: asyncpg.Connection,
    ) -> tuple[tuple[Assertion, ...], tuple[Assertion, ...]]:
        board = await boards.get(connection, board_id)
        settled = await assertions_repo.for_board(connection, board_id)
        return settled, await paraphrases(board, settled)

    settled, restated = _run(work)
    if not settled:
        typer.echo("nothing settled yet")
        return

    noise = {a.statement for a in restated}
    for statement in settled:
        mark = "restates" if statement.statement in noise else "ADDS    "
        typer.echo(f"  {mark}  {statement.statement}")
    typer.echo(
        f"\n{len(settled) - len(restated)}/{len(settled)} carry something the drawing could not say"
    )


# ── rendering ────────────────────────────────────────────────────────────────


def _findings(found: Sequence[BoardFinding]) -> None:
    for finding in sorted(found, key=lambda f: f.severity):
        typer.echo(f"  {finding.severity:<9} {finding.anchor}.{finding.field}  {finding.reason}")


def _thread(thread: Thread, *, with_id: bool = False) -> None:
    head = f"  [{thread.origin} · {thread.status}] {thread.primary_anchor()}"
    typer.echo(f"{head}\n     {thread.id}" if with_id else head)
    typer.echo(f"     Q  {thread.question}")
    if thread.reason:
        typer.echo(f"     ∵  {thread.reason}")
    for message in thread.messages:
        typer.echo(f"     {message.author:>6}  {message.body}")
    typer.echo("")


def main() -> None:
    """Entry point for the `meridian` command."""
    app()


if __name__ == "__main__":
    main()
