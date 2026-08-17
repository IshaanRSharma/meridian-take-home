"""Asking what the drawing does not say, and settling the answers.

A round is a transaction over a board, and the request is that transaction —
threads, situations and settled statements are written together or none of them
are, because `dependencies.connection` yields inside one and rolls back if the
handler raises.

Synchronous, which is a deliberate deviation from `Claude.md` §18. It has this
return a `cycle_id` immediately and the browser follow progress on the `events`
table. The rows are now written — a round emits one per question — but the work
still happens inline, because a round is seconds rather than minutes and a
caller that waits gets its answer in one request instead of subscribing to learn
the same thing. The events are what make the round *legible*; backgrounding it
is a separate change and the function behind this does not move when it lands.
"""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Response, status

from meridian import events
from meridian.api.dependencies import Connection
from meridian.api.schemas import MessageCreate, ThreadUpdate
from meridian.domain.review import Thread
from meridian.repositories import threads as threads_repo
from meridian.reviewer import run

router = APIRouter(tags=["review"])


@router.post("/boards/{board_id}/review", response_model=run.Round)
async def run_round(board_id: UUID, connection: Connection, response: Response) -> run.Round:
    """One round: settle what is answered, then ask what is still worth asking.

    Refuses a drawing that is not yet a workable process. Those findings are ten
    seconds of canvas work and a person can see all of them, so spending a round
    asking somebody to answer what they could simply draw is how a review loop
    earns its reputation.

    The cycle id comes back as a header rather than in the body. A round is
    already a domain object with a meaning, and widening it so a timeline could
    correlate would put an observability concern inside the thing being observed
    — every other consumer of `Round` would then carry a field none of them read.
    """
    cycle_id = uuid4()
    response.headers["X-Cycle-Id"] = str(cycle_id)

    async with events.during(connection, cycle_id=cycle_id, phase="review", kind="round") as detail:
        asked = await run.review(connection, board_id)
        detail.update(
            board_id=str(board_id),
            round=asked.number,
            asked=len(asked.asked),
            resolved=len(asked.resolved),
            reopened=len(asked.reopened),
            walked=len(asked.walked),
        )
        # One row per question, so a timeline reads as questions arriving rather
        # than as a count that landed at once — and each carries its thread, so a
        # line in the feed can open the conversation it refers to.
        for thread in asked.asked:
            await events.emit(
                connection,
                cycle_id=cycle_id,
                phase="review",
                kind="question_asked",
                status="ok",
                thread_id=thread.id,
                primitive_key=next(
                    (a.key for a in thread.anchors if a.kind == "primitive" and a.key), None
                ),
                detail={"category": thread.category, "severity": thread.severity},
            )
    return asked


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
    board_id: UUID,
    connection: Connection,
    # Aliased because `status` is already the FastAPI helper imported above, and
    # the wire contract is `?status=` — a route named after a local import would
    # be the module's problem leaking into somebody else's URL.
    wanted: Annotated[str | None, Query(alias="status")] = None,
) -> list[Thread]:
    """Every question asked about this board, with its anchors and every turn.

    Rejected ones included. A question already dismissed must not be asked
    again, and knowing why it was dismissed is what stops a near-miss re-ask.

    `anchors` is what a canvas draws a pin from, and it arrives with a drawable
    element first — a card or a line, never the bare board. A comment carrying
    only `board` would have nowhere to sit, so it is refused before it is
    stored; the rest are highlighted beside the pin when somebody hovers it.
    """
    found = await threads_repo.for_board(connection, board_id)
    return [t for t in found if wanted is None or t.status == wanted]


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
