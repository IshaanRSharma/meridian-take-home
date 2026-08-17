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

CAP = 6

_SEVERITY: dict[Severity, int] = {"blocking": 0, "important": 1, "minor": 2}


def rank(
    candidates: Sequence[Thread], priors: Sequence[Thread], cap: int = CAP
) -> tuple[Thread, ...]:
    """The questions worth asking this round, best first."""
    # A question the model invented has no structural identity, so its key is
    # None — and "neither of these has a key" must never read as "these are the
    # same question", or every semantic finding in the round collapses into one.
    # Keeping None out of `seen` is what guarantees that, on both sides.
    seen = {prior.decision_key for prior in priors if prior.decision_key}
    fresh: list[Thread] = []
    for candidate in candidates:
        if candidate.decision_key in seen:
            continue
        if candidate.decision_key:
            seen.add(candidate.decision_key)
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
