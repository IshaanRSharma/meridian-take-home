"""The one place a model looks at a board.

Everything else in the review is derived: the situations come from the graph,
the walks come from the interpreter, the blanks come from the rules. This is
where judgement enters — which of those is worth a process owner's attention,
and how to put it in words they can answer.
"""

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from meridian.compiler import decisions as decision_sweeps
from meridian.compiler import serialize
from meridian.core.llm import Task, Transport, structured
from meridian.domain.graph import Board, DryRunResult
from meridian.domain.primitives import Severity
from meridian.domain.review import Anchor, Assertion, Scenario, Thread
from meridian.reviewer import dryrun, prompts, tools
from meridian.reviewer.dryrun import Walked


@dataclass(frozen=True, slots=True)
class Asked:
    """What one model call produced, and what it cost to keep it honest.

    ``probes`` are the situations the model invented and walked itself. They are
    returned rather than discarded because a question raised by a probe can only
    be proved resolved by re-running that probe — and the probes are where the
    best questions come from, so throwing them away would make the good half of
    the review unprovable.

    ``follow_ups`` are ``(thread_id, question)``: a turn to append to a
    conversation that is already open, rather than a new one. An answer that
    hedges — *"usually the supervisor, unless it's urgent"* — has settled
    nothing, and the question that finishes it belongs beside the answer that
    provoked it rather than adrift as a seventh pin on the canvas.

    ``dropped`` is counted rather than logged. Three of the four problems found
    in a day of building this were invisible until someone ran it by hand.

    ``consulted`` is how many times each tool was called, for the same reason.
    A tool exists to make a claim checkable, so a tool nobody calls is a claim
    nobody checked — and from the questions alone the two are indistinguishable.
    """

    comments: tuple[Thread, ...] = ()
    probes: tuple[Scenario, ...] = ()
    follow_ups: tuple[tuple[UUID, str], ...] = ()
    dropped: dict[str, int] = field(default_factory=dict)
    consulted: dict[str, int] = field(default_factory=dict)


class Proposed(BaseModel):
    """One question the model thinks is worth asking."""

    category: Literal[
        "missing_path",
        "ambiguous_rule",
        "missing_context",
        "undefined_exception",
        "undefined_timing",
        "redundancy",
        "spec_gap",
    ]
    severity: Severity
    question: str
    reason: str
    anchors: list[str] = Field(
        description=(
            "Which elements this is about, each written EXACTLY as it appears in "
            "`you may anchor to` — `primitive:<key>`, `edge:<key>`, "
            "`entity_field:<entity>.<field>`, or the bare word `board`. A bare key "
            "with no prefix is not an anchor and the question will be discarded."
        )
    )
    decision_key: str | None = None
    scenario_key: str | None = None
    follows_up: str | None = Field(
        default=None,
        description=(
            "The `id` of a prior question whose answer left something undecided. Set "
            "this ONLY to press on that answer — the question is then appended to that "
            "conversation instead of starting a new one, and `anchors` is ignored "
            "because the conversation already has them. Never set it to restate a "
            "question that was answered, and never on one that was dismissed."
        ),
    )


class Questions(BaseModel):
    """Everything the model wants to ask this round, before any of it is checked."""

    questions: list[Proposed]


async def ask(  # noqa: PLR0913 - a board, what was walked, and three kinds of prior knowledge
    board: Board,
    walked: Walked,
    *,
    threads: tuple[Thread, ...] = (),
    settled: tuple[Assertion, ...] = (),
    round: int = 1,  # noqa: A002 - the review's own word for this
    transport: Transport | None = None,
) -> Asked:
    """Everything the model wants to ask, kept only where the board agrees."""
    probed: dict[str, tuple[Scenario, DryRunResult]] = {}
    answer = await structured(
        Task.REVIEW,
        Questions,
        prompts.SYSTEM,
        prompts.board_for_review(
            json.dumps(shown(board, threads, settled), indent=2),
            dryrun.describe(board, walked),
            "\n".join(anchorable(board)),
        ),
        tools=tools.for_board(board, probed, settled),
        transport=transport,
    )

    keys = {point.key() for point in decision_sweeps.decisions(board)}
    # A question may cite a situation the reviewer enumerated or one the model
    # walked itself. Both are re-runnable; only one of them existed before.
    citable = {scenario.key: (scenario, result) for scenario, result in walked} | probed

    # A conversation may only be pressed on while it is still going. `resolved`
    # means the drawing already shows it and `rejected` means it was considered
    # and set aside, and re-opening either is the fastest way to lose someone.
    open_conversations = {
        str(thread.id): thread
        for thread in threads
        if thread.id and thread.status in ("open", "answered")
    }

    kept: list[Thread] = []
    follow_ups: list[tuple[UUID, str]] = []
    dropped: dict[str, int] = {}
    for proposed in answer.value.questions:
        if proposed.follows_up is not None:
            pressed = open_conversations.get(proposed.follows_up)
            if pressed is None or pressed.id is None:
                dropped["nothing to follow up on"] = dropped.get("nothing to follow up on", 0) + 1
                continue
            follow_ups.append((pressed.id, proposed.question))
            continue

        comment, refused = _checked(board, proposed, keys, citable, round)
        if comment is not None:
            kept.append(comment)
        elif refused:
            dropped[refused] = dropped.get(refused, 0) + 1

    cited = {comment.scenario_key for comment in kept}
    return Asked(
        comments=tuple(kept),
        # Only the probes something actually cites. A walk the model tried and
        # then did not use is a dead row nobody will ever re-run.
        probes=tuple(scenario for key, (scenario, _) in probed.items() if key in cited),
        follow_ups=tuple(follow_ups),
        dropped=dropped,
        consulted=Counter(call.name for call in answer.calls),
    )


def shown(
    board: Board, threads: tuple[Thread, ...] = (), settled: tuple[Assertion, ...] = ()
) -> dict[str, Any]:
    """The board as the model receives it: everything observable, minus the blanks.

    A named function rather than two lines inside the call, because *what the
    model is not shown* is a decision and it should be greppable. The blanks are
    withheld: every one of them is already being put to the owner in the words
    the rule wrote, and they are the most concrete thing in the payload — left
    in, the model spends every question restating them and the questions only it
    can find never get asked. Measured, not assumed: with findings present it
    produced six structural questions and zero about what the values mean.

    Everything remaining has to be announced in the prompt, and a test holds
    these two to each other. A block the model is handed and never told about is
    one it reads as data rather than as part of the job.
    """
    payload = serialize.review_payload(board, threads, assertions=settled)
    payload.pop("findings", None)
    return payload


def anchorable(board: Board) -> tuple[str, ...]:
    """Every element a question may be pinned to, spelled the way it must be written.

    Handing over the vocabulary rather than describing it: the model reliably
    names the right card and reliably drops the prefix, and a question anchored
    to a bare key resolves to nothing and is discarded whole.
    """
    return (
        "board",
        *(f"primitive:{p.key}" for p in board.primitives),
        *(f"edge:{e.key}" for e in board.edges),
        *(
            f"entity_field:{entity.key}.{field}"
            for entity in board.entities()
            for field in entity.config.fields
        ),
    )


def _checked(
    board: Board,
    proposed: Proposed,
    decision_keys: set[str],
    by_scenario: dict[str, tuple[Scenario, DryRunResult]],
    round: int,  # noqa: A002
) -> tuple[Thread | None, str]:
    anchors = tuple(a for a in map(_parsed, proposed.anchors) if a and _resolves(board, a))
    if not anchors:
        return None, "anchor did not resolve"

    # `board` is the one anchor that is not a place. A comment carrying only that
    # cannot be a pin on a card, and comments on the canvas is the product. The
    # pin goes on the first drawable anchor and the rest are highlighted beside
    # it, so ordering matters as much as membership.
    drawable = tuple(a for a in anchors if a.kind in ("primitive", "edge"))
    if not drawable:
        return None, "nothing to pin it to"
    anchors = drawable + tuple(a for a in anchors if a not in drawable)

    cited = by_scenario.get(proposed.scenario_key or "")
    return Thread(
        category=proposed.category,
        # `blocking` means the drawing does not work as a process — a reference
        # that does not resolve, an outcome with no line, a step it stops at
        # without saying so. Provable, visible on the canvas, and closable by
        # anyone. A question about what a value MEANS is never that, however
        # important it is, and the model reaches for the word anyway: on the
        # first live run it marked "what kind of thing is this document" as
        # blocking. Left alone, the strongest word in the system stops meaning
        # anything, and every gate built on it stops being a gate.
        severity="important" if proposed.severity == "blocking" else proposed.severity,
        origin="semantic",
        round=round,
        question=proposed.question,
        reason=proposed.reason,
        anchors=anchors,
        # An identity is derived from the graph or it is not one. Letting the
        # model mint a key would let it collide with a real question and silence
        # it, or invent one nothing else produces so its own never returns.
        decision_key=proposed.decision_key if proposed.decision_key in decision_keys else None,
        scenario_key=cited[0].key if cited else None,
        evidence=dryrun.evidence_for(*cited) if cited else None,
    ), ""


def _parsed(ref: str) -> Anchor | None:
    try:
        return Anchor.parse(ref)
    except ValueError:
        return None


def _resolves(board: Board, anchor: Anchor) -> bool:
    """Whether this names something a process owner could actually be shown."""
    match anchor.kind:
        case "board":
            return True
        case "primitive":
            return board.has(anchor.key or "")
        case "edge":
            return any(edge.key == anchor.key for edge in board.edges)
        case "group":
            return any(p.group_key == anchor.key for p in board.primitives)
        case "entity_field":
            entity, _, path = (anchor.key or "").partition(".")
            return board.entity_has_field(entity, path)
