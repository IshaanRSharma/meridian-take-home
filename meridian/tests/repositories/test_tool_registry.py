"""The registry, and the gate it exists to make possible."""

import asyncpg
import pytest

from meridian.repositories import tools

pytestmark = pytest.mark.db


async def test_every_capability_the_pre_alert_spec_needs_resolves(connection: asyncpg.Connection):
    known = await tools.registry(connection)
    assert {"email.fetch", "email.send", "system.write"} <= set(known)


async def test_a_capability_resolves_to_a_provider_and_an_action(connection: asyncpg.Connection):
    known = await tools.registry(connection)
    assert known["email.send"] == ("composio", "GMAIL_SEND_EMAIL")


async def test_no_credential_is_stored_anywhere_in_the_registry(connection: asyncpg.Connection):
    # The point of the three-layer split. A leaked database must not be a
    # leaked mailbox: the row names a provider, the provider holds the token.
    columns = await connection.fetch(
        "select column_name from information_schema.columns where table_name = 'tools'"
    )
    names = {row["column_name"] for row in columns}
    assert not names & {"token", "secret", "credential", "password", "api_key", "entity_id"}


async def test_a_capability_with_no_row_is_reported_not_invented(connection: asyncpg.Connection):
    # The gate the closed set exists for. If rows appeared on demand this could
    # never fire, and "can we actually build it?" would be checking nothing.
    missing = await tools.unresolvable(connection, ("email.send", "erp.post", "board.lookup"))
    assert missing == ("board.lookup", "erp.post")


async def test_a_spec_that_needs_nothing_external_resolves_trivially(
    connection: asyncpg.Connection,
):
    # A board of nothing but Checks reaches no provider at all.
    assert await tools.unresolvable(connection, ()) == ()


async def test_disabled_rows_are_invisible(connection: asyncpg.Connection):
    # Turning a capability off must make specs using it fail the gate, not
    # silently route to a provider somebody meant to retire.
    await connection.execute("update tools set enabled = false where key = 'email.send'")
    assert "email.send" in await tools.unresolvable(connection, ("email.send",))
