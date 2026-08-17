"""The control surface, exercised the way a person uses it.

These do not test printing. They test the four things that would make the CLI
lie: that a gate refuses, that a refusal says *why* in terms someone can act on,
that the loop actually completes from a terminal, and that a missing board is a
message rather than a traceback.

The commands hold their own transactions, so these run against the local
database directly rather than through the rolled-back `connection` fixture —
which also means each test cleans up after itself by using its own board.
"""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest
from typer.testing import CliRunner

from meridian.cli import app
from meridian.core.config import settings
from meridian.core.db import close_pool, transaction
from meridian.domain.review import Thread
from meridian.repositories import boards
from meridian.repositories import threads as threads_repo
from meridian.reviewer.distill import Distillation
from meridian.reviewer.semantic import Questions
from meridian.seed import COMPLETE, SEED, board_from_file

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not settings().database_url, reason="DATABASE_URL is not set"),
]

runner = CliRunner()


class Turn:
    def __init__(self, parsed: Any) -> None:
        self.output: list[Any] = []
        self.output_parsed = parsed


async def _mute(**kwargs: Any) -> Any:
    """A model that asks nothing and settles nothing."""
    wanted = kwargs.get("text_format")
    return Turn(Distillation(statements=[]) if wanted is Distillation else Questions(questions=[]))


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """No command in this file may reach OpenAI.

    A CLI test that quietly spent money would be found by the bill rather than
    by anyone reading it.
    """
    monkeypatch.setattr("meridian.core.llm._default_transport", lambda: _mute)


def _load(path: Any) -> UUID:
    """Write a board and hand back its id, in a loop of this test's own.

    Sync on purpose. Each command opens a pool and closes it again, because one
    command is one process — so a test sharing pytest's event loop would have
    the pool pulled out from under it by the first invocation.
    """

    async def go() -> UUID:
        try:
            async with transaction() as connection:
                return await boards.save(connection, board_from_file(path))
        finally:
            await close_pool()

    return asyncio.run(go())


def _threads(board_id: UUID) -> tuple[Thread, ...]:
    async def go() -> tuple[Thread, ...]:
        try:
            async with transaction() as connection:
                return await threads_repo.for_board(connection, board_id)
        finally:
            await close_pool()

    return asyncio.run(go())


@pytest.fixture
def drawn() -> UUID:
    """The board as first drawn: three things stop it being a process."""
    return _load(SEED)


@pytest.fixture
def finished() -> UUID:
    """The board as a finished review leaves it."""
    return _load(COMPLETE)


@pytest.fixture
def with_a_gap() -> UUID:
    """Finished but for one thing nobody filled in, so a round has something to ask.

    A workable process with a blank, which is the state the review loop exists
    for — the completed board asks nothing, and the first-drawn one is refused
    before a question can be raised.
    """
    board = board_from_file(COMPLETE)
    without = board.model_copy(
        update={
            "primitives": tuple(
                card.model_copy(
                    update={"config": card.config.model_copy(update={"recipients": ()})}
                )
                if card.key == "report_coa_discrepancy"
                else card
                for card in board.primitives
            )
        }
    )

    async def go() -> UUID:
        try:
            async with transaction() as connection:
                return await boards.save(connection, without)
        finally:
            await close_pool()

    return asyncio.run(go())


# --- the gates refuse, and say why -------------------------------------------


def test_a_drawing_that_is_not_a_process_is_refused_by_name(drawn: UUID) -> None:
    # Three blocking findings, and the point is that each is named in the words
    # the card uses. A gate that reports "3 problems" has turned into a printout.
    result = runner.invoke(app, ["review", "run", str(drawn)])

    assert result.exit_code == 1
    assert "not a process yet" in result.stdout
    assert "coas_valid.outcomes" in result.stdout
    assert "report_coa_discrepancy.outgoing" in result.stdout


def test_lint_reports_what_is_missing_without_refusing(finished: UUID) -> None:
    result = runner.invoke(app, ["board", "lint", str(finished)])

    assert result.exit_code == 0
    assert "nothing missing" in result.stdout


def test_a_board_with_open_questions_does_not_freeze(with_a_gap: UUID) -> None:
    # The gate the whole review exists to protect. Authority transfers to a test
    # suite at the freeze, and it may not transfer while a question is open.
    asked = runner.invoke(app, ["review", "run", str(with_a_gap)])
    assert "1 asked" in asked.stdout

    result = runner.invoke(app, ["spec", "freeze", str(with_a_gap)])

    assert result.exit_code == 1
    assert "unsettled" in result.stdout
    assert "Nobody is named as receiving this." in result.stdout


# --- the loop completes from a terminal --------------------------------------


def test_the_whole_loop_runs_from_the_command_line(finished: UUID) -> None:
    # Ask, answer, settle, freeze. Every step is a command someone can type, and
    # the spec at the end is the thing a code generator reads.
    assert runner.invoke(app, ["review", "run", str(finished)]).exit_code == 0

    for thread in _threads(finished):
        answered = runner.invoke(app, ["thread", "answer", str(thread.id), "The supervisor does."])
        assert answered.exit_code == 0

    frozen = runner.invoke(app, ["spec", "freeze", str(finished)])
    assert frozen.exit_code == 0, frozen.stdout
    assert "frozen  v1" in frozen.stdout

    shown = runner.invoke(app, ["spec", "show", str(finished)])
    assert shown.exit_code == 0
    assert "coas_valid" in shown.stdout


def test_dismissing_a_question_settles_it_too(with_a_gap: UUID) -> None:
    # `rejected` is not a delete. It is settled, so the freeze stops waiting on
    # it — and it still reaches the spec as what is deliberately not done.
    runner.invoke(app, ["review", "run", str(with_a_gap)])
    (asked,) = _threads(with_a_gap)

    dismissed = runner.invoke(app, ["thread", "reject", str(asked.id), "Not applicable here."])
    assert dismissed.exit_code == 0

    frozen = runner.invoke(app, ["spec", "freeze", str(with_a_gap)])
    assert frozen.exit_code == 0, frozen.stdout


# --- and it fails like a tool, not like a stack trace ------------------------


def test_a_board_that_does_not_exist_is_a_message() -> None:
    result = runner.invoke(app, ["board", "lint", str(uuid4())])

    assert result.exit_code == 1
    assert "Not found" in result.stdout
    assert "Traceback" not in result.stdout


def test_nothing_frozen_yet_says_so(finished: UUID) -> None:
    result = runner.invoke(app, ["spec", "show", str(finished)])

    assert result.exit_code == 1
    assert "nothing frozen yet" in result.stdout
