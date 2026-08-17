"""The board, and everything a canvas does to it.

Every handler is two lines: call the function the CLI calls, return what it
returned. The refusals are not caught here — each one is already an object
carrying what a caller needs, and `main.py` renders it once for every route.

Two response shapes, per `Claude.md` §18. These are the CRUD half: they are
synchronous and they return the row, so an optimistic canvas can reconcile
against what was actually stored rather than against what it sent.
"""

from uuid import UUID

from fastapi import APIRouter, status

from meridian.api.dependencies import Connection
from meridian.api.schemas import (
    BoardCreate,
    Described,
    EdgeCreate,
    LayoutUpdate,
    PrimitiveCreate,
    PrimitiveUpdate,
)
from meridian.authoring import create, delete, edit, interpret, layout
from meridian.compiler import rules
from meridian.domain.graph import Board, BoardFinding, Edge, Primitive
from meridian.repositories import boards

router = APIRouter(prefix="/boards", tags=["board"])


@router.get("")
async def list_boards(connection: Connection) -> list[dict[str, object]]:
    """Every board, newest first, with enough to pick one.

    Not `list[Board]`. Rendering a picker does not need eleven cards and their
    edges per row, and loading them would read the whole database to draw ten
    lines of text — so this returns counts instead, computed in SQL.
    """
    return await boards.summaries(connection)


@router.post("", response_model=Board, status_code=status.HTTP_201_CREATED)
async def create_board(body: BoardCreate, connection: Connection) -> Board:
    """Start an empty board."""
    return await create.board(connection, body.name)


@router.get("/{board_id}", response_model=Board)
async def read_board(board_id: UUID, connection: Connection) -> Board:
    """The whole board: cards, lines and where each card sits.

    Always whole. Every consumer wants the primitives and the edges together,
    and `layout` comes with them because a canvas cannot render a node without
    a position.
    """
    return await boards.get(connection, board_id)


@router.get("/{board_id}/lint", response_model=list[BoardFinding])
async def lint_board(board_id: UUID, connection: Connection) -> list[BoardFinding]:
    """What is missing, and how much each one matters.

    Not an error response. `blocking` means the drawing does not work as a
    process; anything else is a question for the review. Nothing blocks while
    somebody is drawing — it bites once, at submit.
    """
    return rules.findings(await boards.get(connection, board_id))


# ── cards ────────────────────────────────────────────────────────────────────


@router.post("/{board_id}/primitives", response_model=Primitive, status_code=201)
async def add_primitive(board_id: UUID, body: PrimitiveCreate, connection: Connection) -> Primitive:
    """Put a card on the board.

    The key is minted here rather than sent, because it is what threads,
    assertions and generated filenames reference — with no foreign key, so a
    caller able to choose it could orphan every comment on a card by renaming it.
    """
    at = (body.x, body.y) if body.x is not None and body.y is not None else None
    return await create.card(
        connection,
        board_id,
        primitive_type=body.primitive_type,
        name=body.name,
        group_key=body.group_key,
        at=at,
    )


@router.patch("/{board_id}/primitives/{key}", response_model=Primitive)
async def update_primitive(
    board_id: UUID, key: str, body: PrimitiveUpdate, connection: Connection
) -> Primitive:
    """Merge some fields onto a card, and hand back what was stored.

    What was stored, never what was sent: the merge revalidates, so a form
    re-renders from the truth rather than from its own optimism.
    """
    return await edit.configure(connection, board_id, key, body.config)


@router.post("/{board_id}/primitives/{key}/describe", response_model=Primitive)
async def describe_primitive(
    board_id: UUID, key: str, body: Described, connection: Connection
) -> Primitive:
    """Say what a card does, and let the fields fill themselves in.

    A typing accelerator, never an authority: it fills blanks only, it keeps the
    sentence verbatim whatever it manages to extract, and anything that has to
    resolve against the board stays for somebody to pick.
    """
    card = (await boards.get(connection, board_id)).p(key)
    patch = await interpret.fields(card, body.said, overwrite=body.overwrite)
    if not patch:
        return card
    return await edit.configure(connection, board_id, key, patch)


@router.delete("/{board_id}/primitives/{key}", response_model=delete.Deletion)
async def remove_primitive(board_id: UUID, key: str, connection: Connection) -> delete.Deletion:
    """Remove a card, and say what it left behind.

    Its connections stay, dangling and reported as blocking, because the person
    who drew them is the one who knows whether the card or the connection was
    the mistake. The response names them so an interface can offer one more
    click rather than deciding on anybody's behalf.
    """
    return await delete.card(connection, board_id, key)


# ── lines ────────────────────────────────────────────────────────────────────


@router.post("/{board_id}/edges", response_model=Edge, status_code=201)
async def add_edge(board_id: UUID, body: EdgeCreate, connection: Connection) -> Edge:
    """Draw one line, carrying the one outcome its handle represents.

    An exact duplicate returns the line already there rather than a second one:
    same ends and same outcome is a double-click, never an intent.
    """
    return await create.connect(
        connection,
        board_id,
        from_key=body.from_key,
        to_key=body.to_key,
        relation=body.relation,
        on_outcomes=tuple(body.on_outcomes),
        condition=body.condition,
    )


@router.delete("/{board_id}/edges/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_edge(board_id: UUID, key: str, connection: Connection) -> None:
    """Rub out one line."""
    await delete.disconnect(connection, board_id, key)


# ── where things sit ─────────────────────────────────────────────────────────


@router.patch("/{board_id}/layout", status_code=status.HTTP_204_NO_CONTENT)
async def move_primitives(board_id: UUID, body: LayoutUpdate, connection: Connection) -> None:
    """Where some cards now sit.

    Debounced by the caller and sent for the cards that moved, so this writes
    one key each rather than the whole canvas — two people dragging two
    different cards never touch the same value.

    Deliberately touches no card row. `primitives` changes when the process
    changes, not when somebody tidies the drawing.
    """
    await layout.rearrange(connection, board_id, body.positions)
