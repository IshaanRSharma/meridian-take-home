"""Threads: a question, the elements it is about, and every turn.

Three tables, because they answer three different questions. `threads` is what
was asked. `comment_anchors` is what it is about, and it is a table rather than a
column because a conversation ranges over several cards at once — that is also
the index the canvas queries to put a pin on a card. `comment_messages` is the
exchange, kept in full rather than summarised, because the process owner's own
vocabulary and their hedges live in the turns.

Read whole, like a board. Every consumer — the reviewer assembling its context,
the canvas drawing pins, the freeze gate counting what is unsettled — wants all
three together.

An anchor may have no key. `board` means the whole process, and there is only
one, so there is nothing to name. Migration 0003 exists because the original
primary key made that unstorable.
"""

import json
from uuid import UUID

import asyncpg

from meridian.domain.review import Anchor, CommentMessage, Evidence, Thread, ThreadStatus

_COMMENTS_SQL = (
    "select id, category, severity, status, origin, round, question, reason, "
    "scenario_key, evidence, decision_key, resolved_at from threads "
    "where board_id = $1 order by round, created_at"
)
_ANCHORS_SQL = (
    "select thread_id, anchor_kind, anchor_key from comment_anchors "
    "where thread_id = any($1::uuid[]) order by is_primary desc, anchor_kind, anchor_key"
)
_MESSAGES_SQL = (
    "select thread_id, seq, author, body, created_at from comment_messages "
    "where thread_id = any($1::uuid[]) order by thread_id, seq"
)
_INSERT_SQL = (
    "insert into threads (board_id, category, severity, status, origin, round, question, "
    "reason, scenario_key, evidence, decision_key) "
    "values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) returning id"
)


async def for_board(connection: asyncpg.Connection, board_id: UUID) -> tuple[Thread, ...]:
    """Every conversation on this board, each with its anchors and its turns.

    Rejected ones included. A question already dismissed must not be asked
    again, and knowing *why* it was dismissed is what stops a near-miss re-ask.
    """
    rows = await connection.fetch(_COMMENTS_SQL, board_id)
    if not rows:
        return ()

    ids = [row["id"] for row in rows]
    anchors: dict[UUID, list[Anchor]] = {}
    for row in await connection.fetch(_ANCHORS_SQL, ids):
        anchors.setdefault(row["thread_id"], []).append(
            Anchor(kind=row["anchor_kind"], key=row["anchor_key"])
        )

    messages: dict[UUID, list[CommentMessage]] = {}
    for row in await connection.fetch(_MESSAGES_SQL, ids):
        messages.setdefault(row["thread_id"], []).append(
            CommentMessage(
                seq=row["seq"], author=row["author"], body=row["body"], created_at=row["created_at"]
            )
        )

    return tuple(
        _thread(row, tuple(anchors.get(row["id"], ())), tuple(messages.get(row["id"], ())))
        for row in rows
    )


async def save(connection: asyncpg.Connection, board_id: UUID, thread: Thread) -> UUID:
    """Write a question, what it is about, and whatever has been said so far."""
    thread_id: UUID = await connection.fetchval(
        _INSERT_SQL,
        board_id,
        thread.category,
        thread.severity,
        thread.status,
        thread.origin,
        thread.round,
        thread.question,
        thread.reason,
        thread.scenario_key,
        json.dumps(thread.evidence.model_dump(mode="json")) if thread.evidence else None,
        thread.decision_key,
    )

    # `is_primary` rather than an ordering column: a set has no order, and what
    # the interface needs is which element the pin goes on.
    await connection.executemany(
        "insert into comment_anchors (thread_id, anchor_kind, anchor_key, is_primary) "
        "values ($1, $2, $3, $4)",
        [
            (thread_id, anchor.kind, anchor.key, index == 0)
            for index, anchor in enumerate(thread.anchors)
        ],
    )
    await connection.executemany(
        "insert into comment_messages (thread_id, seq, author, body) values ($1, $2, $3, $4)",
        [(thread_id, m.seq, m.author, m.body) for m in thread.messages],
    )
    return thread_id


async def set_status(connection: asyncpg.Connection, thread_id: UUID, status: ThreadStatus) -> None:
    """Move a thread, stamping when it settled.

    The transition itself is the domain's to allow — ``Thread.may_become`` owns
    that — so this writes what the caller decided rather than deciding again in
    SQL where the rule would drift from the model.
    """
    await connection.execute(
        "update threads set status = $2, "
        "resolved_at = case when $2 in ('resolved', 'rejected') then now() else null end "
        "where id = $1",
        thread_id,
        status,
    )


async def add_message(
    connection: asyncpg.Connection, thread_id: UUID, author: str, body: str
) -> None:
    """Append a turn. ``seq`` is assigned here so two writers cannot collide."""
    await connection.execute(
        "insert into comment_messages (thread_id, seq, author, body) values ($1, "
        "coalesce((select max(seq) from comment_messages where thread_id = $1), 0) + 1, $2, $3)",
        thread_id,
        author,
        body,
    )


def _thread(
    row: asyncpg.Record, anchors: tuple[Anchor, ...], messages: tuple[CommentMessage, ...]
) -> Thread:
    return Thread(
        id=row["id"],
        category=row["category"],
        severity=row["severity"],
        status=row["status"],
        origin=row["origin"],
        round=row["round"],
        question=row["question"],
        reason=row["reason"],
        scenario_key=row["scenario_key"],
        evidence=_evidence(row["evidence"]),
        decision_key=row["decision_key"],
        resolved_at=row["resolved_at"],
        anchors=anchors,
        messages=messages,
    )


def _evidence(value: object) -> Evidence | None:
    """Asyncpg hands back jsonb as text unless a codec is registered."""
    if value is None:
        return None
    return Evidence.model_validate(json.loads(value) if isinstance(value, str) else value)
