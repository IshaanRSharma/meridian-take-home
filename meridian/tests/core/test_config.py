"""Tests for settings, and for one safety property in particular.

Integration tests roll every transaction back, so nothing they write survives.
That is not the protection that matters. A rollback cannot undo a migration, and
`make db` applies migrations to whatever it is pointed at — so the guarantee has
to be that the suite is never pointed at the production database at all.
"""

import pytest

from meridian.core.config import Settings

LOCAL = "postgresql://meridian:meridian@localhost:54329/meridian?sslmode=disable"
SUPABASE = "postgresql://postgres:secret@db.example.supabase.co:5432/postgres"


def test_tests_default_to_the_local_container_not_production():
    # `make db` starts it. Nobody should have to set an env var to avoid
    # running the suite against a shared database.
    assert Settings(database_url=SUPABASE).requires_test_database() == LOCAL


def test_pointing_the_suite_at_production_is_refused():
    settings = Settings(database_url=SUPABASE, test_database_url=SUPABASE)
    with pytest.raises(RuntimeError, match="same database"):
        settings.requires_test_database()


def test_a_missing_test_database_names_the_command_that_creates_one():
    with pytest.raises(RuntimeError, match="make db"):
        Settings(test_database_url="").requires_test_database()


def test_the_application_still_uses_its_own_database():
    # The two settings are independent: production work reads DATABASE_URL and
    # is unaffected by anything the test harness does.
    assert Settings(database_url=SUPABASE).requires_database() == SUPABASE


def test_a_missing_application_database_says_so_clearly():
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        Settings(database_url="").requires_database()
