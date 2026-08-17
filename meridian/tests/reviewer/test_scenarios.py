"""Tests for the situations a board is asked to account for.

Two properties carry this module. **Coverage**: every outcome a process owner
declared is walked by something, so an outcome nobody drew a line out of cannot
survive a round because the reviewer happened to look elsewhere. **Stability**:
the same board yields the same keys every round, because a thread proves itself
resolved by re-running the scenario that raised it, and that proof is worth
nothing if the key means a different walk in round three.

A scenario is also read by the process owner, so its description is held to
being a sentence about their process rather than about this repository.
"""

from meridian.domain.graph import (
    ActionPrimitive,
    Board,
    CheckPrimitive,
    Edge,
    EntityPrimitive,
    EventPrimitive,
)
from meridian.domain.primitives import (
    ActionConfig,
    CheckConfig,
    Criterion,
    EntityConfig,
    EventConfig,
    FieldRef,
    Outcome,
    Timing,
)
from meridian.domain.review import Scenario
from meridian.reviewer import dryrun
from meridian.reviewer import scenarios as s


def by_key(board: Board) -> dict[str, Scenario]:
    return {scenario.key: scenario for scenario in s.enumerate_from(board)}


# --- what the seed board produces ------------------------------------------


def test_the_seed_board_gets_one_clean_run_and_one_scenario_per_way_it_goes_wrong(seed: Board):
    assert [(sc.key, sc.kind) for sc in s.enumerate_from(seed)] == [
        ("happy_path", "happy"),
        ("invoice_complete_missing_information", "variant"),
        ("coas_valid_missing_coa", "variant"),
        ("coas_valid_mismatched_coa", "variant"),
    ]


def test_every_outcome_the_process_owner_declared_gets_walked(seed: Board):
    # The coverage floor, and the reason this is enumerated rather than asked
    # for: `mismatched_coa` is the outcome with no line out of it, and nothing
    # about it stands out to a model reading the board.
    walked = {(key, answer) for sc in s.enumerate_from(seed) for key, answer in sc.outcomes.items()}
    declared = {(check.key, o.name) for check in seed.checks() for o in check.config.outcomes}
    assert walked == declared


def test_a_scenario_answers_the_checks_and_nothing_else(seed: Board):
    # Events and actions do not come out one way or another, and answering one
    # would put a decision on a card that never makes one.
    checks = {check.key for check in seed.checks()}
    assert all(set(sc.outcomes) == checks for sc in s.enumerate_from(seed))


def test_a_variant_changes_exactly_one_answer(seed: Board):
    # One thing goes wrong at a time. Two would make the walk's result
    # unattributable, which is the whole use a thread makes of it.
    happy, *variants = s.enumerate_from(seed)
    for variant in variants:
        differing = [k for k, answer in variant.outcomes.items() if happy.outcomes[k] != answer]
        assert len(differing) == 1, variant.key


def test_nothing_claims_where_a_scenario_is_expected_to_end_up(seed: Board):
    # Enumeration derives situations from the graph. Where one *should* end up
    # is a statement about the process, and only the process owner has it.
    assert all(sc.expected_terminal is None for sc in s.enumerate_from(seed))


# --- the process owner has to be able to read it ---------------------------


def test_a_scenario_reads_as_a_sentence_about_the_process(seed: Board):
    scenario = by_key(seed)["coas_valid_mismatched_coa"]

    assert scenario.description.startswith("Does every batch on the invoice have a matching COA?")
    assert "'mismatched_coa'" in scenario.description
    # The owner's own words for what that outcome means, carried through.
    assert "a COA exists but its batch number disagrees" in scenario.description
    # And never the key, which means nothing outside this repository.
    assert "coas_valid" not in scenario.description


def test_the_clean_run_names_every_check_it_expects_to_pass(seed: Board):
    happy = s.enumerate_from(seed)[0]
    for check in seed.checks():
        assert check.config.name in happy.description


def test_a_board_with_nothing_to_decide_still_gets_a_clean_run(seed: Board):
    board = Board(
        name="straight through",
        primitives=[
            EventPrimitive(key="arrived"),
            ActionPrimitive(
                key="done", config=ActionConfig(name="Done", effect="noop", is_terminal=True)
            ),
        ],
        edges=[Edge(key="e1", from_key="arrived", to_key="done")],
    )

    (only,) = s.enumerate_from(board)
    assert only.kind == "happy"
    assert only.outcomes == {}
    assert "nothing to decide" in only.description


# --- stability -------------------------------------------------------------


def test_keys_are_stable_across_runs(seed: Board):
    assert [sc.key for sc in s.enumerate_from(seed)] == [sc.key for sc in s.enumerate_from(seed)]


def test_keys_do_not_depend_on_the_order_cards_were_added(seed: Board):
    # A board loaded from a different query order describes the same situations,
    # or a thread stops finding the scenario that raised it.
    shuffled = seed.model_copy(update={"primitives": tuple(reversed(seed.primitives))})
    assert {sc.key for sc in s.enumerate_from(shuffled)} == {
        sc.key for sc in s.enumerate_from(seed)
    }


# --- where a walk begins ---------------------------------------------------


def test_a_board_with_one_way_in_needs_no_starting_point(seed: Board):
    assert all(sc.start is None for sc in s.enumerate_from(seed))


def test_a_board_with_two_ways_in_says_which_one_it_starts_from(two_entries: Board):
    # Without this every scenario on the board is unrunnable, because dry_run
    # refuses to pick an entry point on the caller's behalf.
    assert all(sc.start == "prealert_received" for sc in s.enumerate_from(two_entries))


def test_a_check_the_process_can_come_back_to_is_answered_twice(seed: Board):
    # The bug this exists to stop: the owner answers "it comes back once the
    # problem is fixed", draws the repeat edge that answer implies, and the two
    # situations that raised the question start reporting `loop` — the result
    # that means this process never terminates. Coverage falls for getting it
    # right. A check on a cycle comes out wrong once, then right.

    marked = tuple(
        p.model_copy(update={"config": p.config.model_copy(update={"is_terminal": True})})
        if p.key in ("report_coa_discrepancy", "report_invoice_discrepancy")
        else p
        for p in seed.primitives
    )
    answered = seed.model_copy(
        update={
            "primitives": marked,
            "edges": (
                *seed.edges,
                Edge(
                    key="e6",
                    from_key="coas_valid",
                    to_key="report_coa_discrepancy",
                    relation="exception",
                    on_outcomes=["mismatched_coa"],
                ),
                Edge(
                    key="e7",
                    from_key="report_coa_discrepancy",
                    to_key="coas_valid",
                    relation="repeat",
                ),
            ),
        }
    )

    walked = {s.key: dryrun.walk(answered, s).result for s in s.enumerate_from(answered)}
    assert "loop" not in walked.values(), walked


def test_a_situation_on_a_board_with_no_cycle_is_answered_once(seed: Board):
    for scenario in s.enumerate_from(seed):
        assert all(isinstance(answer, str) for answer in scenario.outcomes.values())


def test_a_long_card_name_still_produces_a_nameable_situation():
    # `enumerate_from` builds eagerly, so one key that overflows `BoardKey` does
    # not cost one scenario — it costs the whole round.

    long_key = "does_every_batch_on_the_invoice_have_a_matching_certificate"
    board = Board(
        name="long",
        primitives=[
            EntityPrimitive(
                key="doc", config=EntityConfig(name="Doc", identified_by="x", fields={"no": {}})
            ),
            EventPrimitive(
                key="arrived",
                config=EventConfig(
                    name="Arrived",
                    channel="email",
                    correlation_key=FieldRef(entity="doc", path="no"),
                    match_condition="x",
                    captures=["doc"],
                    timing=Timing(kind="await"),
                ),
            ),
            CheckPrimitive(
                key=long_key,
                config=CheckConfig(
                    name="Long",
                    inputs=["doc"],
                    criteria=[Criterion(op="present", left=FieldRef(entity="doc", path="no"))],
                    outcomes=[Outcome(name="pass"), Outcome(name="a_rather_long_outcome_name")],
                ),
            ),
        ],
        edges=[Edge(key="x1", from_key="arrived", to_key=long_key)],
    )

    assert len(s.enumerate_from(board)) == 2
