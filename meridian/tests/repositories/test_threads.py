"""Round-trip tests for a conversation.

A thread is stored across three tables — the question, the elements it is about,
and every turn — so the property that matters is that it comes back as one
object with all three intact. Anchors especially: they are how a question
becomes a pin on a card, and a question about the *whole process* has no key at
all, which the schema could not store until migration 0003.
"""

import asyncpg
import pytest

from meridian.core.config import settings
from meridian.domain.review import Anchor, CommentMessage, Evidence, Thread
from meridian.repositories import boards, threads
from meridian.seed import SEED, board_from_file

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not settings().database_url, reason="DATABASE_URL is not set"),
]


ASKED = Thread(
    category="undefined_exception",
    question="Does a COA problem end the process?",
    reason="The SOP ends at reporting and never says what closes the shipment.",
    origin="scenario",
    round=1,
    scenario_key="one_coa_missing",
    decision_key="termination:edge:e5|primitive:report_coa_discrepancy",
    evidence=Evidence(
        tool="dry_run",
        result="dead_end",
        detail={"trace": ["prealert_received", "invoice_complete", "coas_valid"]},
    ),
    anchors=(
        Anchor.parse("primitive:coas_valid"),
        Anchor.parse("edge:e5"),
    ),
    messages=(CommentMessage(seq=1, author="ai", body="Does a COA problem end the process?"),),
)


@pytest.fixture
async def board_id(connection: asyncpg.Connection):
    return await boards.save(connection, board_from_file(SEED))


async def test_a_thread_comes_back_whole(connection: asyncpg.Connection, board_id):
    await threads.save(connection, board_id, ASKED)
    (loaded,) = await threads.for_board(connection, board_id)

    assert loaded.question == ASKED.question
    assert loaded.reason == ASKED.reason
    assert loaded.decision_key == ASKED.decision_key
    assert [str(a) for a in loaded.anchors] == ["primitive:coas_valid", "edge:e5"]
    assert [m.body for m in loaded.messages] == [ASKED.messages[0].body]
    assert loaded.id is not None


async def test_a_question_about_the_whole_process_can_be_stored(
    connection: asyncpg.Connection, board_id
):
    # The bug migration 0003 fixed. A board anchor carries no key, and the old
    # primary key made the column not-null, so an entire class of question —
    # "is there another way information reaches this?" — was unstorable.
    about_the_board = ASKED.model_copy(
        update={"anchors": (Anchor(kind="board"),), "decision_key": "entry:board"}
    )
    await threads.save(connection, board_id, about_the_board)

    (loaded,) = await threads.for_board(connection, board_id)
    assert [str(a) for a in loaded.anchors] == ["board"]


async def test_the_evidence_behind_a_claim_survives(connection: asyncpg.Connection, board_id):
    # A reviewer may explore freely but may not assert something the board does
    # not do. The claim is only as good as the call that produced it, so the
    # call is stored beside it.
    await threads.save(connection, board_id, ASKED)
    (loaded,) = await threads.for_board(connection, board_id)

    assert loaded.evidence is not None
    assert loaded.evidence.tool == "dry_run"
    assert loaded.evidence.result == "dead_end"


async def test_the_first_anchor_is_the_one_it_is_mostly_about(
    connection: asyncpg.Connection, board_id
):
    # Order in a set means nothing, so "primary" is a flag rather than a
    # position — and it has to survive the round trip or the pin lands on the
    # wrong card.
    #
    # The edge is named first here on purpose. Sorted by anything the anchors
    # themselves carry, `edge` comes before `primitive` one way and after it the
    # other, so a query that quietly dropped the flag and sorted by kind would
    # still pass with the anchors the other way round.
    about_the_edge = ASKED.model_copy(
        update={"anchors": (Anchor.parse("edge:e5"), Anchor.parse("primitive:coas_valid"))}
    )
    await threads.save(connection, board_id, about_the_edge)
    (loaded,) = await threads.for_board(connection, board_id)

    assert str(loaded.primary_anchor()) == "edge:e5"


async def test_answering_adds_a_turn_without_losing_the_question(
    connection: asyncpg.Connection, board_id
):
    thread_id = await threads.save(connection, board_id, ASKED)
    await threads.add_message(connection, thread_id, "human", "No — it comes back once corrected.")
    await threads.set_status(connection, thread_id, "answered")

    (loaded,) = await threads.for_board(connection, board_id)
    assert loaded.status == "answered"
    assert [m.author for m in loaded.messages] == ["ai", "human"]
    assert [m.seq for m in loaded.messages] == [1, 2]


async def test_resolving_records_when(connection: asyncpg.Connection, board_id):
    thread_id = await threads.save(connection, board_id, ASKED)
    await threads.set_status(connection, thread_id, "resolved")

    (loaded,) = await threads.for_board(connection, board_id)
    assert loaded.resolved_at is not None


async def test_a_board_with_no_conversation_yet(connection: asyncpg.Connection, board_id):
    assert await threads.for_board(connection, board_id) == ()
