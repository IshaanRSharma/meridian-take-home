"""The HTTP surface, exercised the way a canvas uses it.

These do not test that FastAPI works. They test the three things that would make
the API lie about the system behind it.

**A refusal arrives as something a canvas can act on.** Both gates answer 422
carrying their findings, so a browser pins each one to the card it names instead
of showing a count — which is the same object the CLI prints, from the same
error, so the two cannot drift.

**A request is one transaction.** A round of review writes threads, situations
and statements together or writes none of them, and nothing in a route has to
remember that.

**What comes back is what was stored**, never what was sent. A form re-renders
from the truth, so an optimistic canvas reconciles rather than diverges.

Offline: the transport is monkeypatched, so no request here can reach OpenAI.
"""

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from meridian.api import dependencies
from meridian.api.main import app
from meridian.core.config import settings
from meridian.reviewer.distill import Distillation
from meridian.reviewer.semantic import Questions

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not settings().database_url, reason="DATABASE_URL is not set"),
]


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
    monkeypatch.setattr("meridian.core.llm._default_transport", lambda: _mute)


@pytest.fixture
async def client(connection: asyncpg.Connection) -> AsyncIterator[AsyncClient]:
    """A client whose requests run inside one rolled-back transaction.

    The override is doing two jobs. It points the API at the test container —
    without it `dependencies.connection` reaches for `requires_database()`, which is the
    *production* DSN, and the suite would be writing to Supabase. And it hands
    every request the same connection, so data written by one request is visible
    to the next while nothing is ever committed.
    """
    app.dependency_overrides[dependencies.connection] = lambda: connection
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as talking:
        yield talking
    app.dependency_overrides.clear()


@pytest.fixture
async def board(client: AsyncClient) -> str:
    made = await client.post("/boards", json={"name": "Gym membership freeze"})
    return str(made.json()["id"])


# --- the process is up --------------------------------------------------------


async def test_health_says_so_without_touching_the_database(client: AsyncClient) -> None:
    # A health check that queried would report the API down whenever Postgres
    # was slow, which is the wrong thing to page somebody about and the wrong
    # thing for a platform to restart a container over.
    answered = await client.get("/health")

    assert answered.status_code == 200
    assert answered.json() == {"status": "ok"}


# --- drawing ------------------------------------------------------------------


async def test_a_board_is_created_empty(client: AsyncClient) -> None:
    made = await client.post("/boards", json={"name": "Returns"})

    assert made.status_code == 201
    assert made.json()["primitives"] == []


async def test_a_card_is_dropped_and_comes_back_with_its_key(
    client: AsyncClient, board: str
) -> None:
    # The key is minted server-side, not sent. It is what threads, assertions
    # and generated filenames reference, with no foreign key — a caller able to
    # choose it could orphan every comment on a card by renaming it.
    dropped = await client.post(
        f"/boards/{board}/primitives",
        json={"primitive_type": "check", "name": "Is it in good standing?", "x": 120, "y": 80},
    )

    assert dropped.status_code == 201
    assert dropped.json()["key"] == "is_it_in_good_standing"


async def test_a_blank_card_lints_as_incomplete_rather_than_failing(
    client: AsyncClient, board: str
) -> None:
    # The distinction the whole authoring model rests on: every blank is a
    # question somebody can answer, not an error they have to fix first.
    await client.post(f"/boards/{board}/primitives", json={"primitive_type": "check"})

    found = await client.get(f"/boards/{board}/lint")

    assert found.status_code == 200
    assert {f["field"] for f in found.json()} >= {"name", "criteria", "outcomes"}


async def test_a_value_a_card_cannot_hold_names_the_field(client: AsyncClient, board: str) -> None:
    # 422 with the path, so a form focuses the input rather than showing a
    # banner and leaving somebody to hunt for what was wrong.
    key = (
        await client.post(
            f"/boards/{board}/primitives", json={"primitive_type": "action", "name": "Tell them"}
        )
    ).json()["key"]

    refused = await client.patch(
        f"/boards/{board}/primitives/{key}", json={"config": {"effect": "carrier pigeon"}}
    )

    assert refused.status_code == 422
    assert refused.json()["errors"][0]["field"] == "effect"


async def test_a_refused_edit_leaves_the_stored_card_alone(client: AsyncClient, board: str) -> None:
    # `config` is jsonb with no check constraint, and `boards.get` is the only
    # read path — so a bad write would leave a board nobody could read, and the
    # repair would need the read that no longer works.
    key = (
        await client.post(
            f"/boards/{board}/primitives", json={"primitive_type": "action", "name": "Tell them"}
        )
    ).json()["key"]
    await client.patch(f"/boards/{board}/primitives/{key}", json={"config": {"effect": "notify"}})

    await client.patch(f"/boards/{board}/primitives/{key}", json={"config": {"effect": "pigeon"}})

    stored = await client.get(f"/boards/{board}")
    card = next(p for p in stored.json()["primitives"] if p["key"] == key)
    assert card["config"]["effect"] == "notify"


async def test_a_line_is_drawn_between_two_cards(client: AsyncClient, board: str) -> None:
    first = (
        await client.post(
            f"/boards/{board}/primitives", json={"primitive_type": "event", "name": "It arrives"}
        )
    ).json()["key"]
    second = (
        await client.post(
            f"/boards/{board}/primitives", json={"primitive_type": "check", "name": "Is it ok"}
        )
    ).json()["key"]

    drawn = await client.post(f"/boards/{board}/edges", json={"from_key": first, "to_key": second})

    assert drawn.status_code == 201
    assert drawn.json()["key"] == "e1"


async def test_a_line_to_a_card_that_is_not_there_is_a_404(client: AsyncClient, board: str) -> None:
    first = (
        await client.post(
            f"/boards/{board}/primitives", json={"primitive_type": "event", "name": "It arrives"}
        )
    ).json()["key"]

    refused = await client.post(
        f"/boards/{board}/edges", json={"from_key": first, "to_key": "ghost_card"}
    )

    assert refused.status_code == 404


async def test_removing_a_card_reports_what_it_left_behind(client: AsyncClient, board: str) -> None:
    # Nothing cascades. The response names the orphans so an interface can offer
    # one more click, and the decision stays the owner's.
    first = (
        await client.post(
            f"/boards/{board}/primitives", json={"primitive_type": "event", "name": "It arrives"}
        )
    ).json()["key"]
    second = (
        await client.post(
            f"/boards/{board}/primitives", json={"primitive_type": "check", "name": "Is it ok"}
        )
    ).json()["key"]
    await client.post(f"/boards/{board}/edges", json={"from_key": first, "to_key": second})

    removed = await client.delete(f"/boards/{board}/primitives/{second}")

    assert removed.status_code == 200
    assert [e["key"] for e in removed.json()["dangling"]] == ["e1"]


async def test_dragging_writes_positions_and_nothing_else(client: AsyncClient, board: str) -> None:
    # `primitives` changes when the process changes, not when somebody tidies
    # the canvas — so a drag must not touch a card row.
    key = (
        await client.post(
            f"/boards/{board}/primitives", json={"primitive_type": "check", "name": "Is it ok"}
        )
    ).json()["key"]
    before = (await client.get(f"/boards/{board}")).json()["primitives"]

    moved = await client.patch(f"/boards/{board}/layout", json={"positions": {key: [300.0, 400.0]}})

    assert moved.status_code == 204
    after = (await client.get(f"/boards/{board}")).json()
    assert after["primitives"] == before
    assert after["layout"][key] == {"x": 300.0, "y": 400.0}


async def test_a_board_that_is_not_there_is_a_404(client: AsyncClient) -> None:
    answered = await client.get(f"/boards/{uuid4()}")
    assert answered.status_code == 404


# --- the gates ----------------------------------------------------------------


async def test_a_drawing_that_is_not_a_process_refuses_review_with_its_findings(
    client: AsyncClient, board: str
) -> None:
    # 422 carrying the findings, so a canvas pins each to the card it names.
    # The same object the CLI prints, from the same error.
    await client.post(f"/boards/{board}/primitives", json={"primitive_type": "check"})

    refused = await client.post(f"/boards/{board}/review")

    assert refused.status_code == 422
    assert refused.json()["findings"]


async def test_a_board_with_open_questions_does_not_freeze(client: AsyncClient) -> None:
    # Authority transfers to a test suite at the freeze, and it may not transfer
    # while a question is open.
    made = await client.post("/boards", json={"name": "Returns"})
    board_id = made.json()["id"]
    first = (
        await client.post(
            f"/boards/{board_id}/primitives", json={"primitive_type": "event", "name": "It arrives"}
        )
    ).json()["key"]
    ending = (
        await client.post(
            f"/boards/{board_id}/primitives", json={"primitive_type": "action", "name": "Done"}
        )
    ).json()["key"]
    await client.patch(
        f"/boards/{board_id}/primitives/{ending}",
        json={"config": {"effect": "noop", "is_terminal": True}},
    )
    await client.post(f"/boards/{board_id}/edges", json={"from_key": first, "to_key": ending})
    asked = await client.post(f"/boards/{board_id}/review")
    assert asked.status_code == 200

    refused = await client.post(f"/boards/{board_id}/freeze")

    assert refused.status_code == 422
    assert refused.json()["unsettled"]


async def test_nothing_frozen_yet_is_a_404(client: AsyncClient, board: str) -> None:
    assert (await client.get(f"/boards/{board}/spec")).status_code == 404


# --- a conversation -----------------------------------------------------------


async def test_a_question_can_be_answered_over_http(client: AsyncClient) -> None:
    made = await client.post("/boards", json={"name": "Returns"})
    board_id = made.json()["id"]
    first = (
        await client.post(
            f"/boards/{board_id}/primitives", json={"primitive_type": "event", "name": "It arrives"}
        )
    ).json()["key"]
    ending = (
        await client.post(
            f"/boards/{board_id}/primitives", json={"primitive_type": "action", "name": "Done"}
        )
    ).json()["key"]
    await client.patch(
        f"/boards/{board_id}/primitives/{ending}",
        json={"config": {"effect": "noop", "is_terminal": True}},
    )
    await client.post(f"/boards/{board_id}/edges", json={"from_key": first, "to_key": ending})
    await client.post(f"/boards/{board_id}/review")

    open_now = (await client.get(f"/boards/{board_id}/threads?status=open")).json()
    assert open_now

    moved = await client.patch(
        f"/threads/{open_now[0]['id']}",
        json={"status": "answered", "body": "The duty manager handles it."},
    )

    assert moved.status_code == 204
    after = (await client.get(f"/boards/{board_id}/threads")).json()
    answered = next(t for t in after if t["id"] == open_now[0]["id"])
    assert answered["status"] == "answered"
    assert answered["messages"][-1]["body"] == "The duty manager handles it."


# --- documents ----------------------------------------------------------------
#
# Claude.md §18 names three CRUD surfaces — board, threads, documents — and this
# was the one that stayed a CLI command until a canvas could accept a dropped
# file. A written procedure describes the process rather than flowing through it,
# so it is never a card and never in the frozen spec.


async def test_a_procedure_is_read_and_comes_back_with_its_text(
    client: AsyncClient, board: str
) -> None:
    written = b"1. Check the paperwork.\n2. Report anything wrong within one working day.\n"

    attached = await client.post(
        f"/boards/{board}/documents",
        files={"file": ("procedure.md", written, "text/markdown")},
    )

    assert attached.status_code == 201
    body = attached.json()
    assert body["filename"] == "procedure.md"
    assert body["kind"] == "sop"
    # The text, not just an id. A scan that transcribed badly is invisible
    # otherwise, and it would look like the reviewer ignoring a procedure it had
    # simply never been able to read.
    assert "one working day" in body["text"]
    assert body["id"]


async def test_markdown_is_decoded_rather_than_sent_to_a_model(
    client: AsyncClient, board: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Paying a model to retype a file you can open is waste dressed as
    # sophistication. `offline` already mutes the transport, so this asserts the
    # stronger thing: nothing was called at all.
    async def refuse(**_: Any) -> Any:
        raise AssertionError("a file that is already text must not reach a model")

    monkeypatch.setattr("meridian.core.llm._default_transport", lambda: refuse)

    attached = await client.post(
        f"/boards/{board}/documents",
        files={"file": ("procedure.txt", b"a rule", "text/plain")},
    )

    assert attached.status_code == 201


async def test_a_file_that_cannot_be_read_says_what_would_fix_it(
    client: AsyncClient, board: str
) -> None:
    refused = await client.post(
        f"/boards/{board}/documents",
        files={"file": ("procedure.docx", b"PK\x03\x04", "application/octet-stream")},
    )

    assert refused.status_code == 415
    # Not "upload failed". The extension and the remedy, because one of those
    # sends somebody to read logs and the other is something they can do.
    assert ".docx" in refused.json()["detail"]
    assert "markdown" in refused.json()["detail"]


async def test_documents_come_back_in_a_stable_order(client: AsyncClient, board: str) -> None:
    for name in ("second.md", "first.md"):
        await client.post(
            f"/boards/{board}/documents", files={"file": (name, b"a rule", "text/markdown")}
        )

    listed = await client.get(f"/boards/{board}/documents")

    assert [d["filename"] for d in listed.json()] == ["first.md", "second.md"]


async def test_detaching_a_document_stops_the_next_round_reading_it(
    client: AsyncClient, board: str
) -> None:
    attached = await client.post(
        f"/boards/{board}/documents", files={"file": ("procedure.md", b"a rule", "text/markdown")}
    )
    doc_id = attached.json()["id"]

    gone = await client.delete(f"/boards/{board}/documents/{doc_id}")

    assert gone.status_code == 204
    assert await (await client.get(f"/boards/{board}/documents")).aread() == b"[]"


async def test_a_document_cannot_be_deleted_through_the_wrong_board(
    client: AsyncClient, board: str
) -> None:
    # The id is unguessable, but unguessable is not an authorisation model — and
    # the caller already named a board in the URL, so honouring it costs one
    # predicate.
    attached = await client.post(
        f"/boards/{board}/documents", files={"file": ("procedure.md", b"a rule", "text/markdown")}
    )
    other = str((await client.post("/boards", json={"name": "Another process"})).json()["id"])

    refused = await client.delete(f"/boards/{other}/documents/{attached.json()['id']}")

    assert refused.status_code == 404
    assert len((await client.get(f"/boards/{board}/documents")).json()) == 1


async def test_attaching_to_a_board_that_is_not_there_is_a_404(client: AsyncClient) -> None:
    # Before any work, so an upload against a missing board does not spend a
    # model call finding out.
    refused = await client.post(
        f"/boards/{uuid4()}/documents",
        files={"file": ("scan.pdf", b"%PDF-1.4", "application/pdf")},
    )

    assert refused.status_code == 404
