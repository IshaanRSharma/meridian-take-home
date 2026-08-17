"""The frozen contract, and the moment authority changes hands.

Before the freeze, ground truth about the process lives in a person's head, so
the loop asks a human. After it, ground truth lives in an eval suite. Everything
else exists to make that one transfer safe, which is why the interesting
behaviour here is the refusal — a 422 carrying every blank and every open
question, so a canvas can pin them rather than show a count.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from meridian.api.deps import Connection
from meridian.compiler import freeze as freeze_
from meridian.domain.frozen import FrozenSpec
from meridian.repositories import assertions as assertions_repo
from meridian.repositories import boards, specs
from meridian.repositories import threads as threads_repo
from meridian.reviewer import run

router = APIRouter(tags=["spec"])


@router.post("/boards/{board_id}/freeze", response_model=FrozenSpec, status_code=201)
async def freeze_board(board_id: UUID, connection: Connection) -> FrozenSpec:
    """Seal the board into what a code generator reads.

    Settles first: anything answered since the last round becomes a statement
    here or never does. The whole thing is one transaction, so a spec can never
    exist beside a board that still claims to be in review.
    """
    await run.settle(connection, board_id)
    sealed = freeze_.freeze(
        await boards.get(connection, board_id),
        await assertions_repo.for_board(connection, board_id),
        await threads_repo.for_board(connection, board_id),
        previous=await specs.latest(connection, board_id),
    )
    await specs.save(connection, sealed)
    await boards.mark_submitted(connection, board_id)
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
