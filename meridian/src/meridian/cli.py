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
from meridian.core.db import close_pool, pool, transaction
from meridian.core.llm import LLMError
from meridian.domain.build import Build, EvalCase, Repair, RunResult
from meridian.domain.errors import ConflictingStateError, IncompleteError, NotFoundError
from meridian.domain.frozen import FrozenSpec
from meridian.domain.graph import BoardFinding, Primitive
from meridian.domain.review import Assertion, DocKind, ReferenceDoc, Thread
from meridian.healing import bundle as bundle_
from meridian.healing import record
from meridian.healing import sweep as sweep_
from meridian.healing.compare import compare, score
from meridian.healing.gate import Verdict
from meridian.repositories import assertions as assertions_repo
from meridian.repositories import boards, reference_docs, specs
from meridian.repositories import builds as builds_repo
from meridian.repositories import evals as evals_repo
from meridian.repositories import repairs as repairs_repo
from meridian.repositories import threads as threads_repo
from meridian.reviewer import extract, sufficiency
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
eval_app = typer.Typer(help="Run the suite the spec is measured against.", no_args_is_help=True)
build_app = typer.Typer(help="Attempts at implementing a spec.", no_args_is_help=True)
repair_app = typer.Typer(help="Patches, and what the gate made of them.", no_args_is_help=True)

app.add_typer(board_app, name="board")
app.add_typer(card_app, name="card")
app.add_typer(edge_app, name="edge")
app.add_typer(review_app, name="review")
app.add_typer(thread_app, name="thread")
app.add_typer(spec_app, name="spec")
app.add_typer(eval_app, name="eval")
app.add_typer(build_app, name="build")
app.add_typer(repair_app, name="repair")

BoardId = Annotated[UUID, typer.Argument(help="the board's id")]
ThreadId = Annotated[UUID, typer.Argument(help="the conversation's id")]
CardKey = Annotated[str, typer.Argument(help="the card's key")]
Iteration = Annotated[
    int | None, typer.Option("--build", help="which build; the latest by default")
]
# Where `<slug>/` lives. A parameter rather than a constant because a candidate
# build is an ordinary thing to want to measure before it replaces the one on
# the shelf — and because two people generating the same agent at once should
# not have to take turns over one directory.
AgentsRoot = Annotated[str, typer.Option("--agents", help="directory holding <slug>/")]


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
    except sweep_.AgentNotRunnableError as unrunnable:
        # Distinct from a case that failed: nothing ran, so there is no score to
        # read and no bundle to open. The fix is on disk, not in the suite.
        typer.echo(f"This build cannot be run: {unrunnable}")
        raise typer.Exit(1) from unrunnable
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
    except extract.UnreadableError as unreadable:
        typer.echo(str(unreadable))
        raise typer.Exit(1) from unreadable
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


@board_app.command("attach")
def board_attach(
    board_id: BoardId,
    path: Annotated[Path, typer.Argument(help="a written procedure describing this process")],
    kind: Annotated[DocKind, typer.Option(help="sop · policy · email · other")] = "sop",
) -> None:
    """Attach a written procedure for the reviewer to read.

    Text in, nothing derived. The document describes the process rather than
    flowing through it, so it never becomes a card and never reaches the frozen
    spec — the reviewer reads it to find where the drawing and the procedure
    disagree, which is the one question class a person can check rather than
    argue with.

    Markdown and text are read off disk. A PDF or a photograph of a printed
    sheet goes to the model, which reads the page — no OCR library, because the
    model that reads the procedure can read the scan, and that keeps one seam to
    OpenAI instead of a second parsing stack.
    """
    try:
        text = asyncio.run(extract.text_of(path))
    except (extract.UnreadableError, OSError) as unreadable:
        typer.echo(str(unreadable))
        raise typer.Exit(1) from unreadable
    doc = ReferenceDoc(kind=kind, filename=path.name, text=text)
    attached = _run(lambda c: reference_docs.save(c, board_id, doc, storage_path=str(path)))
    typer.echo(f"attached {doc.filename} ({len(text)} characters read)  {attached}")


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

    # Resolved from the repo root, like `--agents` on sweep and bundle. The two
    # used to disagree: this one was relative to the working directory, so
    # running it from `meridian/` — which you must, because `.env` lives there —
    # wrote `meridian/agents/`, a directory nothing reads and codegen never
    # commits. One rule for where agents live beats two.
    where = (into if into.is_absolute() else _repo_root() / into) / sealed.slug / "spec.lock.json"
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


@spec_app.command("check")
def spec_check(board_id: BoardId) -> None:
    """Whether the frozen spec is enough to build from.

    Claude.md §33's oracle, run per card: hand each entry to a model as its
    implementer and ask what it would have to decide for itself. Everything it
    had to assume is something two competent implementers would settle
    differently — which is the definition of a contract that is not finished.

    Exits non-zero when anything had to be assumed, so this is a gate before
    codegen rather than a printout after it.
    """

    async def work(connection: asyncpg.Connection) -> tuple[int, tuple[sufficiency.Gap, ...]]:
        spec = await specs.latest(connection, board_id)
        if spec is None:
            return 0, ()
        return spec.version, await sufficiency.check(spec)

    version, gaps = _run(work)
    if not version:
        typer.echo("nothing frozen yet")
        return

    if not gaps:
        typer.echo(f"spec v{version}: every card settles what it needs. Ready for codegen.")
        return

    by_card = sufficiency.by_card(gaps)
    typer.echo(f"spec v{version}: {len(gaps)} thing(s) left to whoever builds this\n")
    for key, forced in by_card.items():
        typer.echo(f"  {key}")
        for gap in forced:
            typer.echo(f"      would assume  {gap.assumption}")
            typer.echo(f"      breaks if     {gap.breaks}")
    typer.echo(f"\n{len(by_card)} of {len(by_card)} card(s) with an unsettled decision")
    raise typer.Exit(1)


# ── builds ───────────────────────────────────────────────────────────────────


@build_app.command("register")
def build_register(
    board_id: BoardId,
    from_git: Annotated[
        bool, typer.Option("--from-git", help="read HEAD and the agent's build.json")
    ] = True,
    author: Annotated[str, typer.Option("--as", help="codegen · repair · human")] = "codegen",
    agents: AgentsRoot = "agents",
) -> None:
    """Record the agent on disk as a build of this board's latest spec.

    The step that closes the loop after a skill has run. A coding agent in
    somebody's terminal writes files and commits them; nothing reaches the
    database until this reads the commit back. Without it a session can produce
    five repairs and leave no curve, no history, and nothing for the next
    session to avoid retrying.
    """
    if not from_git:
        typer.echo("--from-git is the only way to register; a build IS a commit")
        raise typer.Exit(1)

    async def work(connection: asyncpg.Connection) -> tuple[Build, str]:
        spec, spec_id = await _spec(connection, board_id)
        built = await record.register(
            connection,
            spec=spec,
            spec_id=spec_id,
            repo_root=_repo_root(),
            cycle_id=sweep_.new_cycle(),
            created_by=author,  # type: ignore[arg-type]
            agents_dir=agents,
        )
        return built, spec.slug

    built, slug = _run(work)
    typer.echo(f"build {built.iteration}  ·  {built.source_ref}  ·  by {built.created_by}")
    typer.echo(f"  entry point   {slug}/{built.entry_point}")
    typer.echo(f"  file_map      {len(built.file_map)} primitive(s)")
    if built.model:
        typer.echo(f"  generated by  {built.model}")


@build_app.command("list")
def build_list(board_id: BoardId) -> None:
    """Every build of this board's spec, oldest first — the curve's x axis."""

    async def work(connection: asyncpg.Connection) -> list[tuple[Build, tuple[int, int]]]:
        _, spec_id = await _spec(connection, board_id)
        rows = []
        for built in await builds_repo.for_spec(connection, spec_id):
            results = await evals_repo.results_for(connection, built.identity)
            rows.append((built, _score(results)))
        return rows

    rows = _run(work)
    if not rows:
        typer.echo("nothing registered yet")
        return
    for built, (passed, total) in rows:
        curve = f"{passed}/{total}" if total else "not swept"
        typer.echo(
            f"  build {built.iteration:<3} {curve:<10} {built.created_by:<8} {built.source_ref}"
        )


# ── the eval suite ───────────────────────────────────────────────────────────


@eval_app.command("load")
def eval_load(
    board_id: BoardId,
    source: Annotated[Path, typer.Option("--from", help="a JSON array of cases")],
) -> None:
    """Load the suite this spec is measured against.

    Keyed on `(spec, key)`, so re-loading a corrected fixture corrects the case
    rather than adding a second one — two rows for one shipment would double its
    weight in every score computed afterwards.
    """
    cases = [EvalCase.model_validate(case) for case in json.loads(source.read_text())]

    async def work(connection: asyncpg.Connection) -> tuple[int, tuple[str, ...]]:
        spec, spec_id = await _spec(connection, board_id)
        for case in cases:
            await evals_repo.save_case(connection, spec_id, case)
        return len(cases), await record.unattributable_columns(
            connection, spec=spec, spec_id=spec_id
        )

    loaded, unfilled = _run(work)
    typer.echo(f"{loaded} case(s) loaded from {source}")
    if unfilled:
        # Not an error. A column nothing fills still scores — as a permanent
        # failure that localises to no file and no repair can move — so it never
        # announces itself, and the right moment to hear about it is now, while
        # the board can still be edited.
        typer.echo(
            f"\n  {len(unfilled)} column(s) no card on this board fills: {', '.join(unfilled)}"
        )
        typer.echo("  they will score as failures for ever, and no bundle can name a file for them")


@eval_app.command("sweep")
def eval_sweep(  # noqa: PLR0913, PLR0917 - what to run, against which build, for how long
    board_id: BoardId,
    iteration: Iteration = None,
    split: Annotated[str | None, typer.Option(help="train · holdout; both by default")] = None,
    case: Annotated[list[str] | None, typer.Option(help="run only these keys")] = None,
    seconds: Annotated[
        float, typer.Option("--timeout", help="give up on a case after this long")
    ] = sweep_.CASE_TIMEOUT_SECONDS,
    agents: AgentsRoot = "agents",
) -> None:
    """Run every case through the build and record what happened.

    Per column, because a patch that fixes one and breaks another leaves the row
    failing before and after — so a row-level score watches a regression go past
    without changing.

    `--timeout` is worth knowing about. An exception in Temporal workflow code
    is a workflow task failure, which retries forever — so a broken agent does
    not fail, it *hangs*, and the timeout is the only thing that turns silence
    back into a result. The default suits real work; drop it while iterating,
    because the wait is the whole cost of a bad build.
    """

    async def work(connection: asyncpg.Connection) -> sweep_.Swept:
        spec, spec_id = await _spec(connection, board_id)
        built = await _build(connection, spec_id, iteration)
        cases = await evals_repo.cases_for(connection, spec_id, split=split, keys=case)
        if not cases:
            raise NotFoundError(
                f"no eval cases for this spec — `meridian eval load {board_id} --from <file>`"
            )
        # A second connection, outside the sweep's transaction, carrying the
        # per-case events only. Without it a watcher sees nothing until the
        # whole run commits, which on a live suite is the entire run.
        watcher = await (await pool()).acquire()
        try:
            return await sweep_.sweep(
                connection,
                build=built,
                spec=spec,
                cases=cases,
                agents_root=_repo_root() / agents,
                cycle_id=sweep_.new_cycle(),
                case_timeout=seconds,
                progress=watcher,
            )
        finally:
            await (await pool()).release(watcher)

    _sweep_report(_run(work), split)


@eval_app.command("case")
def eval_case(
    board_id: BoardId,
    key: Annotated[str, typer.Argument(help="the case to run, e.g. CAAU4056270")],
    iteration: Iteration = None,
    seconds: Annotated[float, typer.Option("--timeout", help="give up after this long")] = 60.0,
    agents: AgentsRoot = "agents",
) -> None:
    """Run one case. The first thing to try after a patch.

    A shorter default timeout than a full sweep, because this is the iterating
    command and a hang is the failure mode you meet most while iterating.
    """
    eval_sweep(
        board_id, iteration=iteration, split=None, case=[key], seconds=seconds, agents=agents
    )


@app.command("run")
def run_agent(  # noqa: PLR0913, PLR0917 - one board, and the four knobs the steps take
    board_id: BoardId,
    case: Annotated[list[str] | None, typer.Option(help="run only these keys")] = None,
    cases_from: Annotated[
        Path | None, typer.Option("--from", help="load the suite from here first")
    ] = None,
    author: Annotated[str, typer.Option("--as", help="codegen · repair · human")] = "codegen",
    seconds: Annotated[
        float, typer.Option("--timeout", help="give up on a case after this long")
    ] = sweep_.CASE_TIMEOUT_SECONDS,
    agents: AgentsRoot = "agents",
) -> None:
    """Take the agent on disk and run it: register, sweep, and print the failure.

    Three commands that have no decision between them. After a skill has written
    or patched an agent, `build register`, `eval sweep` and `bundle` are always
    run in that order and the middle one is meaningless without the first — so
    typing them separately is ceremony, and getting the order wrong is a
    confusing error rather than a wrong answer.

    Deliberately not a fourth step. `repair record` stays its own command
    because it takes a judgement — which failure this patch was aimed at, and
    whether it is a defect or a spec gap — and a command that guessed either
    would be recording a decision nobody made.

    Every sub-step keeps its own command. This is a shortcut for the loop, not
    a replacement for being able to run one part of it.

    **Commit the agent first.** `source_ref` is `agents/<slug>@<sha>`, so a build
    is a commit — registering an uncommitted directory would name a commit that
    does not contain it. The refusal says so, and this does not commit on
    anybody's behalf.
    """
    if cases_from is not None:
        eval_load(board_id, cases_from)

    build_register(board_id, from_git=True, author=author, agents=agents)
    typer.echo("")
    eval_sweep(board_id, None, None, case, seconds, agents)
    typer.echo("")
    # Only when something failed. A clean sweep printing a failure block would
    # be printing the absence of one, and the block is long.
    try:
        bundle(board_id, None, None, Path(agents))
    except (NotFoundError, IncompleteError):
        typer.echo("  nothing to repair")


@app.command("bundle")
def bundle(
    board_id: BoardId,
    iteration: Iteration = None,
    signature: Annotated[
        str | None, typer.Option(help="which failure; the largest bucket by default")
    ] = None,
    agents: Annotated[Path, typer.Option(help="where agent directories live")] = Path("agents"),
) -> None:
    """The failure block, ready to paste. This is the product.

    Self-contained by design: the file to open, the values that disagreed, the
    values that failed, what was skipped, what review already settled about the
    step, and what has already been tried. A reader who has to resolve an
    identifier has left the paste, and at that point they may as well have read
    the failure themselves.
    """

    async def work(connection: asyncpg.Connection) -> str:
        spec, spec_id = await _spec(connection, board_id)
        built = await _build(connection, spec_id, iteration)
        return await bundle_.bundle(
            connection,
            build=built,
            spec=spec,
            signature=signature,
            agents_root=_repo_root() / agents,
        )

    typer.echo(_run(work))


# ── repairs ──────────────────────────────────────────────────────────────────


@repair_app.command("record")
def repair_record(  # noqa: PLR0913, PLR0917 - what, where, why, and against what
    board_id: BoardId,
    signature: Annotated[str, typer.Option(help="the failure this patch was aimed at")],
    summary: Annotated[str, typer.Option(help="what changed, and why that was the cause")],
    classification: Annotated[
        str, typer.Option("--class", help="implementation_defect · spec_gap")
    ] = "implementation_defect",
    iteration: Iteration = None,
    thread_id: Annotated[
        UUID | None, typer.Option("--thread", help="required for a spec gap")
    ] = None,
) -> None:
    """Record a patch, and let the gate decide what to call it.

    Which cases were failing, which regressed, and therefore the status are all
    read from the two sweeps that bracket the patch rather than supplied. That
    is the whole point of a gate: it can only reject, a human is required to
    override it, and neither means anything if the numbers it judges on came
    from whoever wrote the patch.
    """

    async def work(connection: asyncpg.Connection) -> tuple[Repair, Verdict | None]:
        spec, spec_id = await _spec(connection, board_id)
        built = await _build(connection, spec_id, iteration)
        return await record.record_repair(
            connection,
            build=built,
            spec=spec,
            signature=signature,
            classification=classification,  # type: ignore[arg-type]
            summary=summary,
            cycle_id=sweep_.new_cycle(),
            repo_root=_repo_root(),
            thread_id=thread_id,
        )

    repair, verdict = _run(work)
    typer.echo(f"repair {repair.id}  ·  {repair.status}")
    if repair.raised_thread_id:
        typer.echo(f"  thread   {repair.raised_thread_id}  — back to the process owner")
    typer.echo(f"  {verdict.reason if verdict else 'spec gap — not gated, a person decides'}")
    if repair.files_touched:
        typer.echo(f"  touched  {', '.join(repair.files_touched)}")
    if repair.status in ("regressed", "rejected"):
        typer.echo("\n  the gate can only reject — `meridian repair accept` overrides it")
        raise typer.Exit(1)


@repair_app.command("accept")
def repair_accept(
    repair_id: Annotated[UUID, typer.Argument(help="the repair's id")],
) -> None:
    """Override the gate. A human is required for this, never for approval."""

    async def work(connection: asyncpg.Connection) -> Repair:
        await repairs_repo.set_status(connection, repair_id, "accepted")
        return await repairs_repo.get(connection, repair_id)

    typer.echo(f"accepted  ·  {_run(work).summary}")


@repair_app.command("history")
def repair_history(
    signature: Annotated[str, typer.Argument(help="the failure signature")],
) -> None:
    """What has already been tried against this failure.

    Across builds, not within one. Without it the same failed idea gets retried
    across sessions, which is the specific waste a human-run loop suffers.
    """
    tried = _run(lambda c: repairs_repo.history_for(c, signature))
    if not tried:
        typer.echo("none")
        return
    for repair in tried:
        typer.echo(f"  [{repair.status}] {repair.summary}")


# ── resolving what a command was pointed at ──────────────────────────────────


async def _spec(connection: asyncpg.Connection, board_id: UUID) -> tuple[FrozenSpec, UUID]:
    """This board's latest spec, and the row everything after it references."""
    spec = await specs.latest(connection, board_id)
    spec_id = await specs.latest_id(connection, board_id)
    if spec is None or spec_id is None:
        raise NotFoundError(f"board {board_id} has nothing frozen — `meridian spec freeze` first")
    return spec, spec_id


async def _build(connection: asyncpg.Connection, spec_id: UUID, iteration: int | None) -> Build:
    """A build by its iteration, or the most recent one.

    Iterations rather than ids on the command line: `--build 3` is what a person
    reads off the curve, and a uuid is what they would have to look up first.
    """
    if iteration is None:
        latest = await builds_repo.latest(connection, spec_id)
        if latest is None:
            raise NotFoundError("nothing registered yet — `meridian build register` first")
        return latest
    for built in await builds_repo.for_spec(connection, spec_id):
        if built.iteration == iteration:
            return built
    raise NotFoundError(f"no build {iteration} for this spec")


def _repo_root() -> Path:
    """Where `agents/` lives, from git rather than from the shell's position."""
    root = record.repo_root(Path.cwd())
    if root is None:
        raise NotFoundError("not inside a git repository, so `agents/<slug>@<sha>` has no meaning")
    return root


# ── rendering ────────────────────────────────────────────────────────────────


def _sweep_report(swept: sweep_.Swept, split: str | None) -> None:
    errored = swept.errored()
    scope = f" · {split}" if split else ""
    typer.echo(
        f"\nSWEEP{scope}  {swept.passed}/{swept.total} columns   "
        f"{len(swept.results)} cases   {len(errored)} errored\n"
    )
    for result in swept.results:
        state = "ERROR" if result.errored else ("ok" if result.passed() else "FAIL")
        detail = result.errored or ", ".join(m.column for m in result.mismatched)
        typer.echo(
            f"  {state:5}  {result.key:16} {len(result.matched)}/{result.columns()}  {detail}"
        )

    if swept.rejected_steps:
        # Rule 4: a trace step is named after a card. One that is not cannot be
        # stored, and saying so beats a silent hole in the trajectory.
        typer.echo(
            f"\n  {len(swept.rejected_steps)} step name(s) are not card keys and were "
            f"not recorded: {', '.join(swept.rejected_steps)}"
        )
    if swept.empty:
        typer.echo(
            "\n  EMPTY SWEEP — every case counted nothing, so every column expecting zero\n"
            "  scored as a pass. The failure is upstream of everything the eval measures."
        )
    if swept.passed < swept.total:
        typer.echo("\n  `meridian bundle` for the largest failing bucket")


def _score(results: Sequence[RunResult]) -> tuple[int, int]:
    return score(
        [compare(r.case_key, r.expected_output, r.output, errored=r.errored) for r in results]
    )


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
