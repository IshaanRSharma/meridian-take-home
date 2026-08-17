"""The frozen contract, and the moment authority changes hands.

Before the freeze, ground truth about the process lives in a person's head, so
the loop asks a human. After it, ground truth lives in an eval suite. Everything
else exists to make that one transfer safe, which is why the interesting
behaviour here is the refusal — a 422 carrying every blank and every open
question, so a canvas can pin them rather than show a count.
"""

from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Response, status

from meridian import events
from meridian.api.dependencies import Connection
from meridian.compiler import freeze as freeze_
from meridian.domain.frozen import FrozenSpec
from meridian.repositories import assertions as assertions_repo
from meridian.repositories import boards, specs
from meridian.repositories import threads as threads_repo
from meridian.reviewer import run, sufficiency
from meridian.reviewer.distill import paraphrases

router = APIRouter(tags=["spec"])


@router.post("/boards/{board_id}/freeze", response_model=FrozenSpec, status_code=201)
async def freeze_board(board_id: UUID, connection: Connection, response: Response) -> FrozenSpec:
    """Seal the board into what a code generator reads.

    Settles first: anything answered since the last round becomes a statement
    here or never does. The whole thing is one transaction, so a spec can never
    exist beside a board that still claims to be in review.

    Emits under `compile` rather than `review`: this is the moment authority
    stops being a person's and starts being a test suite's, and a timeline that
    filed it beside the questions would bury the single most consequential row
    in the system among them.
    """
    cycle_id = uuid4()
    response.headers["X-Cycle-Id"] = str(cycle_id)

    async with events.during(
        connection, cycle_id=cycle_id, phase="compile", kind="freeze"
    ) as detail:
        settled = await run.settle(connection, board_id)
        sealed = freeze_.freeze(
            await boards.get(connection, board_id),
            await assertions_repo.for_board(connection, board_id),
            await threads_repo.for_board(connection, board_id),
            previous=await specs.latest(connection, board_id),
        )
        await specs.save(connection, sealed)
        await boards.mark_submitted(connection, board_id)
        detail.update(
            board_id=str(board_id),
            version=sealed.version,
            checksum=sealed.checksum,
            slug=sealed.slug,
            primitives=len(sealed.primitives),
            entities=len(sealed.entities),
            settled_now=len(settled),
        )
    return sealed


@router.get("/boards/{board_id}/spec", response_model=FrozenSpec)
async def read_spec(board_id: UUID, connection: Connection) -> FrozenSpec:
    """The most recent spec frozen from this board.

    Each card arrives with its statements already inlined, so a reader joins
    nothing — which is the same reason the spec view can show a card with what
    was settled about it without a second request.
    """
    sealed = await specs.latest(connection, board_id)
    if sealed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="nothing frozen yet")
    return sealed


@router.get("/boards/{board_id}/spec/sufficiency", response_model=list[sufficiency.Gap])
async def spec_sufficiency(board_id: UUID, connection: Connection) -> list[sufficiency.Gap]:
    """Every decision this spec still leaves to whoever implements it.

    Claude.md §33's oracle, per card: each entry goes to a model as its
    implementer, which reports what it would have to decide for itself. An empty
    list is the answer worth reaching, and it is why this is a GET rather than a
    gate that refuses — the spec is already frozen and immutable, so what a caller
    can do with this is open a conversation, not block a write.

    Slow by nature: one model call per card. The FDE surface shows it on demand
    rather than on load, for the same reason `spec yield` is its own command.
    """
    sealed = await specs.latest(connection, board_id)
    if sealed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="nothing frozen yet")
    return list(await sufficiency.check(sealed))


@router.get("/boards/{board_id}/spec/yield")
async def spec_yield(board_id: UUID, connection: Connection) -> dict[str, object]:
    """How much of what was settled the drawing could not have said itself.

    A statement that only restates the graph is correctly anchored, correctly
    inlined, correctly checksummed and worth nothing. Both halves come back —
    every statement and which ones restate — because the number alone invites
    somebody to chase it, and the interesting object is *which* statements added
    something.

    Reported, never dropped: a restatement is still true, and losing a settled
    answer to a model's judgement is the worse failure.
    """
    board = await boards.get(connection, board_id)
    settled = await assertions_repo.for_board(connection, board_id)
    restated = {a.statement for a in await paraphrases(board, settled)}
    return {
        "settled": len(settled),
        "carries_something_new": len(settled) - len(restated),
        "statements": [
            {
                "statement": a.statement,
                "anchor": str(a.anchor),
                "restates_the_drawing": a.statement in restated,
            }
            for a in settled
        ],
    }
