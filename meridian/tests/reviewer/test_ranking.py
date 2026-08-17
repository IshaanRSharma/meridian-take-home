"""Tests for what a process owner is actually shown.

The reviewer can always produce more questions than anyone will read, so the
cap is not a performance measure — it is the whole difference between a review
someone answers and a form they abandon. Two properties carry this file: a
question already dealt with never comes back, and what survives is ordered by
what it is worth rather than by the order it happened to be generated in.
"""

from meridian.domain.review import Evidence, Thread
from meridian.reviewer import ranking
from meridian.reviewer.ranking import CAP, rank


def asked(key: str | None, **overrides) -> Thread:
    base = {
        "category": "missing_path",
        "question": f"question about {key}",
        "decision_key": key,
    }
    return Thread(**(base | overrides))


def keys(threads) -> list[str | None]:
    return [t.decision_key for t in threads]


# --- nothing is asked twice -------------------------------------------------


def test_a_question_already_asked_is_not_asked_again():
    prior = asked("termination:edge:e5", status="answered")
    assert ranking.rank([asked("termination:edge:e5")], [prior]) == ()


def test_a_question_that_was_dismissed_does_not_come_back():
    # The one that matters most. `rejected` is not a delete — it is considered
    # and set aside, and re-raising it is the fastest way to make someone stop
    # reading. Dedup has to cover every status, not just the open ones.
    for settled in ("rejected", "resolved", "answered", "open"):
        prior = asked("entry:primitive:prealert_received", status=settled)
        survived = ranking.rank([asked("entry:primitive:prealert_received")], [prior])
        assert survived == (), f"a {settled} question came back"


def test_two_generators_finding_the_same_thing_ask_once():
    same = [asked("sequence:edge:e2"), asked("sequence:edge:e2")]
    assert len(ranking.rank(same, [])) == 1


def test_questions_nobody_derived_are_never_confused_with_each_other():
    # A model-invented question has no structural identity, so its key is None.
    # Treating "both have no key" as "these are the same question" would collapse
    # every semantic finding in the round into one.
    #
    # The prior below is what makes this bite: it also has no key, so a dedup
    # that did not guard against None would put None into the set and silently
    # drop everything the model came up with this round.
    invented = [
        asked(None, question="Who is the ops manager?"),
        asked(None, question="What makes a shipment urgent?"),
    ]
    prior_without_a_key = asked(None, question="Something asked last round.")

    assert len(ranking.rank(invented, [prior_without_a_key])) == 2


def test_a_question_nobody_derived_is_still_not_asked_word_for_word_twice():
    # The other half of the test above, and the half that was missing. A key is
    # how a question is recognised next round, and a semantic question has none —
    # so nothing recognised it, and three rounds over an unedited board asked the
    # same six questions three times each, character-identical.
    #
    # It is not untidiness. Every duplicate is an open thread and open threads
    # block the freeze, so a board reviewed three times could not be frozen until
    # somebody answered the same question three times.
    same = "Who receives the report when the supervisor is away?"
    prior = asked(None, question=same, status="answered")

    assert ranking.rank([asked(None, question=same)], [prior]) == ()


def test_the_same_question_typed_differently_is_still_the_same_question():
    # Only what is free and certain. Casing and spacing vary run to run because
    # the sentence is regenerated, not stored — but a genuine paraphrase gets
    # through, and catching those is `unasked()`'s job, not this one's.
    prior = asked(None, question="Who receives the report?", status="rejected")
    respaced = asked(None, question="  who   receives the report?  ")

    assert ranking.rank([respaced], [prior]) == ()


def test_a_new_question_still_gets_through():
    prior = asked("entry:primitive:prealert_received")
    survived = ranking.rank([asked("termination:edge:e5")], [prior])
    assert keys(survived) == ["termination:edge:e5"]


# --- what survives, and in what order ---------------------------------------


def test_no_more_than_the_cap_is_ever_shown():
    many = [asked(f"kind:element_{n}") for n in range(20)]
    assert len(ranking.rank(many, [], cap=6)) == 6


def test_something_that_blocks_is_asked_before_something_that_merely_matters():
    survived = ranking.rank(
        [asked("a", severity="minor"), asked("b", severity="blocking"), asked("c")], []
    )
    assert keys(survived) == ["b", "c", "a"]


def test_a_question_backed_by_a_trace_outranks_one_that_is_only_plausible():
    # Both are worth asking; only one can be shown to be about something the
    # board actually does. That is the difference between a question the owner
    # trusts and one they argue with.
    #
    # The cited one is keyed `z` on purpose. Keyed `a` it would sort first on the
    # tiebreak alone, and a ranking that ignored evidence entirely would still
    # produce the expected answer.
    cited = asked("z", evidence=Evidence(tool="dry_run", result="dead_end"))
    survived = ranking.rank([asked("a"), cited], [])
    assert keys(survived) == ["z", "a"]


def test_the_same_round_asked_twice_asks_the_same_things():
    # Two runs on one board must agree, or the determinism check on the reviewer
    # is measuring the sort rather than the model.
    candidates = [asked(f"kind:element_{n}") for n in range(10)]
    shuffled = list(reversed(candidates))
    assert ranking.rank(candidates, [], cap=4) == ranking.rank(shuffled, [], cap=4)


def test_a_round_with_nothing_new_to_ask_is_allowed_to_be_empty():
    assert ranking.rank([], []) == ()


# --- neither source starves the other ----------------------------------------


def blank(n: int, **overrides) -> Thread:
    return Thread(
        category="missing_context",
        origin="lint",
        question=f"Nothing says {n}.",
        decision_key=f"lint:primitive:report_x:field_{n}",
        **overrides,
    )


def found(n: int, **overrides) -> Thread:
    return Thread(
        category="ambiguous_rule",
        origin="semantic",
        question=f"What counts as the same {n}?",
        **overrides,
    )


def test_a_round_carries_both_what_was_proved_and_what_was_noticed():
    # Six blanks and five invented questions is the seed board exactly. Sorted
    # together they were separated by question text, alphabetically, and five of
    # the six blanks were cut every round — four of them on the reporting steps,
    # so across a whole review nobody was ever asked who receives a report.
    kept = rank([blank(n) for n in range(6)] + [found(n) for n in range(5)], [])

    # Both sources share the round; the split is the cap's business, not
    # this test's. What must never happen again is one source cut entirely.
    assert len([c for c in kept if c.origin == "lint"]) >= 1
    assert len([c for c in kept if c.origin == "semantic"]) >= 1


def test_one_source_having_nothing_does_not_waste_the_slots():
    kept = rank([found(n) for n in range(9)], [])
    assert len(kept) == CAP


def test_how_much_it_matters_still_beats_where_it_came_from():
    # Alternating is a tiebreak between sources, not a licence to put a minor
    # question in front of an important one.
    kept = rank([blank(0, severity="minor"), found(0, severity="important")], [], cap=1)

    assert kept[0].origin == "semantic"
