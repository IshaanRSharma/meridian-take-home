"""What a request is given.

One transaction per request, and it is the request that decides whether it
commits: the dependency yields a connection inside a transaction, and the
context manager rolls it back if the handler raises. So a round of review writes
its threads, its situations and its statements together or writes none of them,
without any route having to remember that.

The same `core.db.transaction` the CLI uses, so both callers get the same
guarantee from the same place rather than two implementations that agree until
somebody changes one.
"""

from collections.abc import AsyncIterator
from typing import Annotated

import asyncpg
from fastapi import Depends

from meridian.core.db import transaction


async def connection() -> AsyncIterator[asyncpg.Connection]:
    """A connection inside a transaction, for the life of one request."""
    async with transaction() as held:
        yield held


Connection = Annotated[asyncpg.Connection, Depends(connection)]
