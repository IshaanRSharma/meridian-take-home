"""Asking what the drawing does not say, and settling the answers.

A round is a transaction over a board, and the request is that transaction —
threads, situations and settled statements are written together or none of them
are, because `deps.connection` yields inside one and rolls back if the handler
raises.

Synchronous, which is a deliberate deviation from `Claude.md` §18. It has this
return a `cycle_id` immediately and the browser follow progress on the `events`
table; `events.py` does not exist yet, so a caller handed a `cycle_id` would
have no way to learn the outcome — worse than waiting. The function behind this
does not change when it lands.
"""

from uuid import UUID

from fastapi import APIRouter, status

from meridian.api.deps import Connection
from meridian.api.schemas import MessageCreate, ThreadUpdate
from meridian.domain.review import Thread
from meridian.repositories import threads as threads_repo
from meridian.reviewer import run

router = APIRouter(tags=["review"])


@router.post("/boards/{board_id}/review", response_model=run.Round)
async def run_round(board_id: UUID, connection: Connection) -> run.Round:
    """One round: settle what is answered, then ask what is still worth asking.

    Refuses a drawing that is not yet a workable process. Those findings are ten
    seconds of canvas work and a person can see all of them, so spending a round
    asking somebody to answer what they could simply draw is how a review loop
    earns its reputation.
    """
    return await run.review(connection, board_id)


@router.post("/boards/{board_id}/settle", response_model=list[str])
async def settle_answers(board_id: UUID, connection: Connection) -> list[str]:
    """Turn finished conversations into statements, and close what is proved.

    The freeze runs this too. A review that ends with the last question answered
    has no next round to distil it, so without this the spec would be sealed
    missing exactly the knowledge the final round produced.
    """
    return [str(thread_id) for thread_id in await run.settle(connection, board_id)]


@router.get("/boards/{board_id}/threads", response_model=list[Thread])
async def read_threads(
    board_id: UUID, connection: Connection, thread_status: str | None = None
) -> list[Thread]:
    """Every question asked about this board, with its anchors and every turn.

    Rejected ones included. A question already dismissed must not be asked
    again, and knowing why it was dismissed is what stops a near-miss re-ask.
    """
    found = await threads_repo.for_board(connection, board_id)
    return [t for t in found if thread_status is None or t.status == thread_status]


@router.post("/threads/{thread_id}/messages", status_code=status.HTTP_204_NO_CONTENT)
async def add_message(thread_id: UUID, body: MessageCreate, connection: Connection) -> None:
    """Append a turn without moving the conversation on."""
    await threads_repo.add_message(connection, thread_id, "human", body.body)


@router.patch("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def move_thread(thread_id: UUID, body: ThreadUpdate, connection: Connection) -> None:
    """Answer a question, or dismiss it.

    `resolved` is not reachable from here, deliberately. `answered` means the
    knowledge exists; that the drawing shows it is proved by the next round
    re-running the walk that raised the question, and nobody gets to declare it.
    """
    if body.body:
        await threads_repo.add_message(connection, thread_id, "human", body.body)
    await threads_repo.set_status(connection, thread_id, body.status)
