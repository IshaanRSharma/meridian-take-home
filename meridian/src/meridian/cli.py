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
import json
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Annotated
from uuid import UUID

import asyncpg
import typer
from pydantic import ValidationError

from meridian.authoring import create, delete, edit, interpret, layout
from meridian.compiler import freeze as freeze_
from meridian.compiler import rules
from meridian.core.db import close_pool, transaction
from meridian.core.llm import LLMError
from meridian.domain.errors import ConflictingStateError, IncompleteError, NotFoundError
from meridian.domain.graph import BoardFinding, Primitive
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
card_app = typer.Typer(help="The cards on a board.", no_args_is_help=True)
edge_app = typer.Typer(help="The lines between them.", no_args_is_help=True)
thread_app = typer.Typer(help="Conversations about a board.", no_args_is_help=True)
spec_app = typer.Typer(help="The frozen contract a code generator reads.", no_args_is_help=True)

app.add_typer(board_app, name="board")
app.add_typer(card_app, name="card")
app.add_typer(edge_app, name="edge")
app.add_typer(review_app, name="review")
app.add_typer(thread_app, name="thread")
app.add_typer(spec_app, name="spec")

BoardId = Annotated[UUID, typer.Argument(help="the board's id")]
ThreadId = Annotated[UUID, typer.Argument(help="the conversation's id")]
CardKey = Annotated[str, typer.Argument(help="the card's key")]


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
    except ValidationError as invalid:
        # A value somebody supplied, not an infrastructure failure — so it must
        # come before the `asyncpg` arm below, which exits 2 and says the
        # database is unreachable. `loc` is the path to the field, which is what
        # a form needs in order to focus the right input.
        typer.echo(f"{invalid.error_count()} value(s) this card cannot hold:")
        for error in invalid.errors():
            where = ".".join(str(part) for part in error["loc"])
            typer.echo(f"  {where}  {error['msg']}")
        raise typer.Exit(1) from invalid
    except LLMError as unavailable:
        # Distinct from an unreachable database because the fix is different:
        # nothing is wrong with the board, and pressing save again is the whole
        # remedy. The prose is still in front of whoever typed it.
        typer.echo(f"The model could not be reached: {unavailable}")
        raise typer.Exit(3) from unavailable
    except (OSError, asyncpg.PostgresError) as unreachable:
        typer.echo(f"Cannot reach the database: {unreachable}")
        raise typer.Exit(2) from unreachable


# ── the board ────────────────────────────────────────────────────────────────


@board_app.command("new")
def board_new(name: Annotated[str, typer.Argument(help="what this process is called")]) -> None:
    """Start an empty board and print its id."""
    board = _run(lambda c: create.board(c, name))
    typer.echo(str(board.id))


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


# ── cards and connections ────────────────────────────────────────────────────


@card_app.command("add")
def card_add(
    board_id: BoardId,
    primitive_type: Annotated[str, typer.Option("--type", help="event · action · check · entity")],
    name: Annotated[str | None, typer.Option(help="what it is called")] = None,
    at: Annotated[str | None, typer.Option(help="where it sits, as x,y")] = None,
) -> None:
    """Put a card on the board and print the key everything else refers to."""
    placed = _run(
        lambda c: create.card(
            c,
            board_id,
            primitive_type=primitive_type,  # type: ignore[arg-type]
            name=name,
            at=_point(at),
        )
    )
    typer.echo(placed.key)


@card_app.command("set")
def card_set(
    board_id: BoardId,
    key: CardKey,
    fields: Annotated[str, typer.Option("--json", help='e.g. \'{"effect": "notify"}\'')],
) -> None:
    """Set some fields on a card.

    The engineer's way in, not the process owner's — the canvas writes the same
    fields through the same call, from a form and a set of dropdowns.
    """
    card = _run(lambda c: edit.configure(c, board_id, key, json.loads(fields)))
    typer.echo(json.dumps(card.config.model_dump(mode="json", exclude_none=True), indent=2))


@card_app.command("describe")
def card_describe(
    board_id: BoardId,
    key: CardKey,
    said: Annotated[str, typer.Argument(help="what this step does, in your own words")],
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="replace what is already filled in")
    ] = False,
) -> None:
    """Say what a card does, and let the fields fill themselves in.

    A typing accelerator, never an authority. It fills blanks only, it keeps
    your sentence verbatim whatever it manages to extract from it, and anything
    that has to point at something else on the board stays for you to pick —
    because a plausible wrong answer there looks exactly like a right one.

    The model call runs first, in its own transaction, so a board lock is never
    held across it.
    """
    card = _run(lambda c: boards.get(c, board_id)).p(key)
    patch = asyncio.run(_described(card, said, overwrite=overwrite))
    if not patch:
        typer.echo("nothing in that sentence settled a field")
        return

    filled = _run(lambda c: edit.configure(c, board_id, key, patch))
    typer.echo(json.dumps(filled.config.model_dump(mode="json", exclude_none=True), indent=2))


async def _described(card: Primitive, said: str, *, overwrite: bool) -> dict[str, object]:
    return await interpret.fields(card, said, overwrite=overwrite)


@card_app.command("rm")
def card_rm(board_id: BoardId, key: CardKey) -> None:
    """Remove a card, and say what it left behind.

    Its connections stay, dangling and reported as blocking, because the person
    who drew them is the one who knows whether the card or the connection was
    the mistake.
    """
    gone = _run(lambda c: delete.card(c, board_id, key))

    typer.echo(f"removed {gone.key}")
    for edge in gone.dangling:
        typer.echo(f"  {edge.key} now points at nothing: {edge.from_key} ──▶ {edge.to_key}")
    for other in gone.still_named_by:
        typer.echo(f"  {other} still names it")


@card_app.command("move")
def card_move(
    board_id: BoardId,
    key: CardKey,
    at: Annotated[str, typer.Argument(help="where it goes, as x,y")],
) -> None:
    """Put a card somewhere on the canvas."""
    point = _point(at)
    if point is None:
        typer.echo("a position looks like 120,80")
        raise typer.Exit(1)
    _run(lambda c: layout.move(c, board_id, key, x=point[0], y=point[1]))


@edge_app.command("add")
def edge_add(
    board_id: BoardId,
    from_key: Annotated[str, typer.Argument(help="the card the line leaves")],
    to_key: Annotated[str, typer.Argument(help="the card it arrives at")],
    on: Annotated[str | None, typer.Option(help="the outcome this line carries")] = None,
    relation: Annotated[str, typer.Option(help="normal · exception · repeat")] = "normal",
) -> None:
    """Draw one line. One outcome per line, the way one drag makes one edge."""
    line = _run(
        lambda c: create.connect(
            c,
            board_id,
            from_key=from_key,
            to_key=to_key,
            relation=relation,  # type: ignore[arg-type]
            on_outcomes=(on,) if on else (),
        )
    )
    typer.echo(line.key)


@edge_app.command("rm")
def edge_rm(board_id: BoardId, key: Annotated[str, typer.Argument(help="the line's key")]) -> None:
    """Rub out one line."""
    _run(lambda c: delete.disconnect(c, board_id, key))
    typer.echo(f"removed {key}")


def _point(at: str | None) -> tuple[float, float] | None:
    """`"120,80"` as somewhere on the canvas, or nothing."""
    if not at:
        return None
    x, _, y = at.partition(",")
    try:
        return (float(x), float(y))
    except ValueError:
        return None


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


@spec_app.command("export")
def spec_export(
    board_id: BoardId,
    into: Annotated[Path, typer.Option(help="where agent directories live")] = Path("agents"),
) -> None:
    """Write the frozen spec to `agents/<slug>/spec.lock.json`.

    The file a code generator is handed. It is written from what was sealed and
    stored, never re-derived from the board — a board that has moved on since
    the freeze must not quietly change the contract somebody approved.

    The slug is the spec's own, minted at v1 and carried across versions, so v2
    lands in the same directory and every repair stays a reviewable diff.
    """
    sealed = _run(lambda c: specs.latest(c, board_id))
    if sealed is None:
        typer.echo("nothing frozen yet")
        raise typer.Exit(1)

    where = into / sealed.slug / "spec.lock.json"
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(
        json.dumps(
            {
                "version": sealed.version,
                "checksum": sealed.checksum,
                **json.loads(sealed.payload()),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    typer.echo(f"{where}  ·  v{sealed.version}  ·  {sealed.checksum[:12]}")


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
