"""Load a board from a JSON file into the database.

The seed file is the same shape the canvas will POST and the same shape
``Board`` validates, so loading it exercises the round trip rather than a
special path that only the seed uses.
"""

import asyncio
import json
from pathlib import Path
from uuid import UUID

from meridian.core.db import close_pool, transaction
from meridian.domain.graph import Board
from meridian.repositories import boards

SEED = Path(__file__).resolve().parents[2] / "db" / "seeds" / "prealert_board.json"


def board_from_file(path: Path) -> Board:
    """Parse a seed file into a board, validating every card on the way."""
    raw = json.loads(path.read_text())
    return Board(
        name=raw["board"]["name"],
        status=raw["board"]["status"],
        review_round=raw["board"]["review_round"],
        primitives=raw["primitives"],
        edges=raw["edges"],
        layout=raw["layout"],
    )


async def load(path: Path = SEED) -> UUID:
    """Write the seed board and return its id."""
    board = board_from_file(path)
    async with transaction() as connection:
        return await boards.save(connection, board)


async def _main() -> None:
    board_id = await load()
    print(f"seeded {board_id}")  # noqa: T201 - this is a command, printing is the point
    await close_pool()


if __name__ == "__main__":
    asyncio.run(_main())
