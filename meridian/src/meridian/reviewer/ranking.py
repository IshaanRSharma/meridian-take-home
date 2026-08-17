"""What a process owner is actually shown.

The generators can always produce more than anyone will read. Six per round is
not a performance budget — it is the difference between a review someone answers
and a form they abandon, and the questions that get cut are cheap to recover:
the structural ones are derived from the graph, so they come back next round if
the board still deserves them.

Two rules, and the first matters more. **A question already dealt with never
returns**, in any status — a rejected question coming back is the fastest way to
lose someone's attention, and `rejected` means considered and set aside rather
than deleted. Dedup is an exact match on the identity the graph derived, never a
guess at whether two sentences mean the same thing, because the model rewords
every round and near-misses would slip through.

Second, the two sources share the round rather than compete for it. Both
produce more than six every time, so whatever separates them decides everything
a person sees — and one of them is provable while the other is the only place
certain questions can come from. Neither deserves to win outright.
"""

from collections.abc import Sequence
from itertools import zip_longest

from meridian.domain.primitives import Severity
from meridian.domain.review import Thread

# Three, not six. A round is a sitting: the person answering has to hold the
# whole set in their head to notice that two of the questions are really one, and
# six is past where that stops happening — they get skimmed, and a skimmed
# question is answered badly rather than not at all.
#
# It costs nothing to be small. Overflow carries to the next round rather than
# being dropped, and rounds differ in kind anyway, so more of them is the shape
# the loop already wanted. What does not move is the freeze gate: every question
# still has to be settled, because that is where authority transfers.
CAP = 3

_SEVERITY: dict[Severity, int] = {"blocking": 0, "important": 1, "minor": 2}


def rank(
    candidates: Sequence[Thread], priors: Sequence[Thread], cap: int = CAP
) -> tuple[Thread, ...]:
    """The questions worth asking this round, best first."""
    seen = {_identity(prior) for prior in priors}
    fresh: list[Thread] = []
    for candidate in candidates:
        if _identity(candidate) in seen:
            continue
        seen.add(_identity(candidate))
        fresh.append(candidate)

    chosen: list[Thread] = []
    for severity in _SEVERITY:
        tier = [comment for comment in fresh if comment.severity == severity]
        chosen += _alternating(
            sorted([c for c in tier if c.origin == "lint"], key=_worth),
            sorted([c for c in tier if c.origin != "lint"], key=_worth),
        )
    return tuple(chosen[:cap])


def _alternating(provable: list[Thread], found: list[Thread]) -> list[Thread]:
    """One from each source in turn, so neither starves the other.

    Both sources reliably produce more than a round can spend, so whatever
    breaks the tie between them decides the whole round. Sorting the pool
    together broke it on question *text*, alphabetically, which is arbitrary —
    and on the seed board it meant six provable blanks lost to five invented
    questions almost every time. Four of those six were on the reporting steps,
    so across a two-round review nobody was ever asked who receives a report.

    Not a ratio, because there is no defensible number. Alternating says only
    that a question a rule can prove and a question only a model can find are
    both worth a slot, which is the actual claim.
    """
    taken: list[Thread] = []
    for one, other in zip_longest(provable, found):
        taken += [comment for comment in (one, other) if comment is not None]
    return taken


def _identity(comment: Thread) -> str:
    """What makes two questions the same question across rounds.

    A lint question is derived from the graph, so it has a `decision_key` and
    that key is the identity — which is why round one's blanks never came back.

    A question the model invented has none: letting it mint one would let it
    collide with a derived key and silence a real question, or invent one
    nothing else produces so its own never returns. So `decision_key` is None,
    and for a while nothing recognised those at all — three rounds over an
    unedited board asked the same six questions three times each, word for word,
    and every duplicate was an open thread standing between that board and its
    freeze.

    The text is the fallback, and only because these were **character-identical**.
    Two invented questions in one round say different things and keep their own
    identities, which is the property that must survive; the same question next
    round is the same string. Casing and spacing are normalised because the
    sentence is regenerated rather than stored. Nothing cleverer belongs here —
    a genuine paraphrase needs a model to recognise it, which is `unasked()`,
    and `distill.paraphrases` is already the shape for it.
    """
    return comment.decision_key or " ".join(comment.question.casefold().split())


def _worth(comment: Thread) -> tuple[int, int, str]:
    """Severity, then whether it can point at something, then a stable tiebreak.

    Evidence is the second key because a question citing a walk the board
    actually took is one the owner can check, while a plausible one is a
    question they argue with. Ranking by value is otherwise unsolved — the
    signals that would do it properly, like how often a kind of question gets
    rejected, only exist once there is more than one deployment to learn from.
    """
    return (
        _SEVERITY[comment.severity],
        0 if comment.evidence else 1,
        comment.decision_key or comment.question,
    )
