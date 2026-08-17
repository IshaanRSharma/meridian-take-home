"""One round of review.

A round is a transaction over a board, not a chat turn. It reads the drawing and
every conversation had about it, works out what is still worth asking, and writes
the questions back. **It never writes a card or an edge** — the only board column
it touches is the round counter. If the reviewer could redraw the board it would
be marking its own homework, and `resolved` would stop meaning anything.

The order matters. Resolution is settled *first*, against the board as it now
stands, because a question answered last round is either shown by this drawing or
it is not — and that has to be decided before anything new is asked, or the round
asks about gaps it is in the middle of closing.

Three sources, in descending order of how sure they are:

    blanks       a rule found a field nobody filled. Provable, and asked in the
                 words the rule already wrote — no model involved, because the
                 sentence on the card *is* the question.
    the model    what a rule cannot see: what the values mean, and what the
                 drawing quietly decided.
    the cap      six, because a review someone answers beats a form they abandon.

The model never sees the blanks. Measured: shown them, it spends every question
restating them and asks nothing about what the values mean.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

import asyncpg

from meridian.compiler import rules
from meridian.core.llm import Transport
from meridian.domain.errors import IncompleteError
from meridian.domain.graph import Board, BoardFinding
from meridian.domain.review import Anchor, Assertion, Scenario, Thread
from meridian.repositories import assertions as assertions_repo
from meridian.repositories import boards, reference_docs
from meridian.repositories import scenarios as scenarios_repo
from meridian.repositories import threads as threads_repo
from meridian.reviewer import distill, dryrun, ranking, reference, scenarios, semantic


@dataclass(frozen=True, slots=True)
class Round:
    """What one round did, in the terms a person would ask about it."""

    number: int
    asked: tuple[Thread, ...] = ()
    resolved: tuple[UUID, ...] = ()
    reopened: tuple[UUID, ...] = ()
    walked: tuple[Scenario, ...] = ()
    dropped: dict[str, int] = field(default_factory=dict)
    consulted: dict[str, int] = field(default_factory=dict)


async def review(
    connection: asyncpg.Connection, board_id: UUID, *, transport: Transport | None = None
) -> Round:
    """Ask what is still worth asking about this board.

    Refuses a drawing that is not yet a workable process. Those findings are ten
    seconds of canvas work and a person can see all of them; spending a round
    asking someone to answer what they could simply draw is how a review loop
    earns its reputation.
    """
    board = await boards.get(connection, board_id)
    blocking = rules.blocking(board)
    if blocking:
        raise NotReadyForReviewError(blocking)

    resolved = await settle(connection, board_id, transport=transport)
    # Read again: settling just moved statuses and wrote statements, and asking
    # against the board as it stood a moment ago would re-open what it closed.
    prior = await threads_repo.for_board(connection, board_id)
    settled = await assertions_repo.for_board(connection, board_id)

    walked = [
        (situation, dryrun.walk(board, situation)) for situation in scenarios.enumerate_from(board)
    ]
    number = board.review_round + 1

    # What any attached procedure says, aligned onto the elements it bears on.
    # The only question class where a disagreement is checkable rather than
    # speculative, and empty on every board that has no document — which is most
    # of them, and the reason the whiteboard exists.
    documented = await reference.read(
        board, await reference_docs.for_board(connection, board_id), transport=transport
    )

    asked = await semantic.ask(
        board,
        walked,
        threads=prior,
        settled=settled,
        documented=documented,
        round=number,
        transport=transport,
    )
    # Six things to read, counting these. A follow-up finishes a conversation
    # rather than starting one, so it takes its slot first.
    pressing = asked.follow_ups[: ranking.CAP]
    reopened = await _press(connection, prior, pressing)

    candidates = [*_from_blanks(board, number), *asked.comments]
    kept = ranking.rank(candidates, list(prior), cap=ranking.CAP - len(pressing))

    for situation, result in walked:
        await scenarios_repo.save(connection, board_id, situation)
        await scenarios_repo.record_result(connection, board_id, situation.key, result.result)
    for probe in asked.probes:
        await scenarios_repo.save(connection, board_id, probe)

    for comment in kept:
        await threads_repo.save(connection, board_id, comment)
    await boards.set_review_round(connection, board_id, number)

    return Round(
        number=number,
        asked=tuple(await threads_repo.for_board(connection, board_id))[len(prior) :],
        resolved=resolved,
        reopened=reopened,
        walked=tuple(situation for situation, _ in walked) + asked.probes,
        dropped=asked.dropped,
        consulted=asked.consulted,
    )


async def settle(
    connection: asyncpg.Connection, board_id: UUID, *, transport: Transport | None = None
) -> tuple[UUID, ...]:
    """Turn every finished conversation into statements, and close what is proved.

    **Public, and the freeze path has to call it too.** A round settles what was
    answered since the last one, which covers every answer except the ones that
    matter most: the usual flow is review, answer everything, freeze, and those
    last answers have no next round to be distilled by. Freezing without calling
    this seals a spec missing exactly the knowledge the final round produced.

    Two things happen here, and only the second is about status.

    **Every conversation that has finished distils**, whether it was answered or
    dismissed. A dismissal is knowledge too — *"expiry is not checked at this
    stage"* stops a later round re-asking and tells a code generator the case was
    considered rather than missed. Statements are the only thing that crosses the
    freeze, so a conversation that never distils is one the spec never hears.

    **Resolution is proved, not declared**, and how depends on what was asked. A
    question raised by a situation is closed by re-running that situation: if it
    ends somewhere else now, the drawing changed to show the answer. A question
    about what a value *means* has no situation behind it, so what proves it is
    that the answer produced a statement at all — *"yeah, it depends"* distils to
    nothing and stays open, which is the right outcome.

    Distilling once is what the assertion check is for. Without it every round
    re-distils every settled thread and the same sentence lands on the same card
    again and again.
    """
    board = await boards.get(connection, board_id)
    prior = await threads_repo.for_board(connection, board_id)
    recorded = await assertions_repo.for_board(connection, board_id)
    # Kept, not reduced to ids. The distiller has to be shown what the elements
    # it is about already know, or two conversations settle the same fact onto
    # one card and the spec inlines the sentence twice.
    already = {a.thread_id for a in recorded}
    resolved: list[UUID] = []

    for comment in prior:
        if comment.id is None or comment.status not in ("answered", "rejected"):
            continue

        # Distil once, but keep asking whether the drawing shows it. These are
        # not the same question: an answer becomes a statement the moment it is
        # given, while the redraw that proves it may be three rounds away, and
        # skipping both together strands every question whose owner answered
        # before they edited the canvas.
        statements: tuple[Assertion, ...] = ()
        if comment.id not in already:
            statements = await distill.distil(board, comment, recorded, transport=transport)
            for statement in statements:
                await assertions_repo.save(connection, board_id, statement)

        if comment.status == "rejected":
            continue
        if _redrawing_is_the_answer(comment):
            proved = not dryrun.reproduces(board, comment.evidence)  # type: ignore[arg-type]
        else:
            proved = bool(statements)
        if proved:
            await threads_repo.set_status(connection, comment.id, "resolved")
            resolved.append(comment.id)

    return tuple(resolved)


def _redrawing_is_the_answer(comment: Thread) -> bool:
    """Whether this question is closed by editing the drawing rather than by an answer.

    A question is resolved when whatever raised it has changed. For these two,
    what raised it is a walk falling off the graph — an outcome with no line, a
    failure path that never comes back — so the proof is that the same walk now
    ends somewhere else, and nobody gets to declare it by answering.

    Every other question was raised by silence, and a statement ends silence.
    The distinction matters because the model cites a walk far more often than a
    walk is the point: it attached one to *"do all the line items get checked
    before anything is reported"*, which no redraw will ever change. Judged on
    presence of evidence alone, that conversation could never close, and a
    question nobody can ever settle blocks the freeze for good.
    """
    return (
        comment.evidence is not None
        # A walk is the only citation a redraw can change. A question that cited
        # the list of places two values have to match is closed by somebody
        # saying what a match is, and re-running that list would return the same
        # joins forever.
        and comment.evidence.tool == "dry_run"
        and comment.category in ("missing_path", "undefined_exception")
    )


async def _press(
    connection: asyncpg.Connection,
    prior: tuple[Thread, ...],
    follow_ups: Sequence[tuple[UUID, str]],
) -> tuple[UUID, ...]:
    """Append the reviewer's next turn to conversations whose answer settled nothing.

    A hedge — *"usually the supervisor, unless it's urgent"* — names two
    recipients and defines neither, and the question that finishes it belongs
    beside the answer that provoked it rather than adrift as a new pin on a card
    the person has to place again.

    Pressing on an answer un-answers it. ``answered`` means the knowledge exists,
    and the moment the reviewer says the answer left something undecided, it does
    not — which is also what keeps the thread blocking the freeze.
    """
    by_id = {thread.id: thread for thread in prior}
    reopened: list[UUID] = []
    for thread_id, question in follow_ups:
        await threads_repo.add_message(connection, thread_id, "ai", question)
        if by_id[thread_id].status == "answered":
            await threads_repo.set_status(connection, thread_id, "open")
            reopened.append(thread_id)
    return tuple(reopened)


def _from_blanks(board: Board, number: int) -> list[Thread]:
    """Every field nobody filled in, asked in the words the rule wrote.

    Deterministic on purpose. `reason` is already a sentence a warehouse
    supervisor can answer — putting it through a model would only risk it coming
    back worse, and would spend a slot the model needs for what only it can see.
    """
    return [
        Thread(
            category="missing_context",
            severity=finding.severity,
            origin="lint",
            round=number,
            question=finding.reason,
            reason=f"Nothing on the board says it, and {finding.field} is where it would go.",
            anchors=(Anchor.parse(finding.anchor),),
            # Findings already merge on (anchor, field), so this pair is unique
            # by construction — which makes it exactly the identity dedup needs.
            decision_key=f"lint:{finding.anchor}:{finding.field}",
        )
        for finding in rules.findings(board)
        if finding.severity != "blocking" and finding.anchor != "board"
    ]


class NotReadyForReviewError(IncompleteError):
    """A board that is not yet a workable process.

    Subclasses `IncompleteError` so the boundary keeps mapping it to a 422 with
    findings — the same shape the freeze refuses with, because it is the same
    kind of refusal one gate earlier.
    """

    def __init__(self, findings: list[BoardFinding]) -> None:
        """Carry the findings, because the caller has to name them."""
        self.findings = findings
        super().__init__(f"{len(findings)} things to draw before this can be reviewed")
