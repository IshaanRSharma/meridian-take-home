"""The tool registry: rows in, the mapping ``Tools`` takes out.

Read whole, once, at worker start. It is a handful of rows that change when
somebody writes a migration, so a query per capability would be a round trip to
learn something already known.

Nothing here reads a credential, because none is stored. A row says which
provider performs a capability and under what action name; the provider holds
the secret.
"""

import asyncpg

_REGISTRY_SQL = "select key, provider, action from tools where enabled order by key"


async def registry(connection: asyncpg.Connection) -> dict[str, tuple[str, str]]:
    """Every enabled capability, as ``(provider, action)``.

    Plain tuples rather than ``runtime.tools.Tool``. A repository reaching into
    the runtime would be an upward import — and the runtime is the one package
    generated agents carry into a worker process, so it must not acquire a
    dependency on anything that talks to a database. The composition root builds
    the runtime type from these rows.
    """
    rows = await connection.fetch(_REGISTRY_SQL)
    return {row["key"]: (row["provider"], row["action"]) for row in rows}


async def unresolvable(
    connection: asyncpg.Connection, capabilities: tuple[str, ...]
) -> tuple[str, ...]:
    """Which of a spec's capabilities the registry cannot satisfy.

    The pre-codegen gate, and the reason the registry is a closed set. A
    capability with no row cannot be conjured — it needs a provider action, an
    adapter, or a conversation with the process owner about whether the step can
    be automated at all.
    """
    known = await registry(connection)
    return tuple(sorted(set(capabilities) - set(known)))
