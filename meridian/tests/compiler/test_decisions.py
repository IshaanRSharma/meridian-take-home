"""Tests for the decisions a drawing makes.

Three properties matter more than any individual sweep, and they are what
separate this from lint.

A **finished** board still makes decisions. Lint goes silent when every field is
filled; decisions do not, because a complete drawing still chose to end where it
ends. If a sound board produced nothing here, this would be lint wearing a
different hat.

A decision **never restates a lint finding**. `mismatched_coa` leading nowhere is
*missing*, so lint owns it. A decision about it would put one problem on two
surfaces.

Keys are **stable**. The same board yields the same identities every run,
because dedup three units later is an exact match on them rather than a guess at
whether two sentences mean the same thing.
"""

from meridian.compiler import decisions as d
from meridian.compiler import rules
from meridian.domain.graph import ActionPrimitive, Board, CheckPrimitive, Edge
from meridian.domain.primitives import ActionConfig, CheckConfig, Outcome, Timing


def kinds(points: list[d.DecisionPoint]) -> dict[str, int]:
    counted: dict[str, int] = {}
    for point in points:
        counted[point.kind] = counted.get(point.kind, 0) + 1
    return counted


# --- what separates a decision from a finding ------------------------------


def test_a_finished_board_still_makes_decisions(sound: Board):
    # Lint is silent here. Decisions are not, and must not be: a complete
    # drawing still chose to end where it ends and to wait as long as it waits.
    assert rules.findings(sound) == []
    assert d.decisions(sound) != []


def test_no_decision_restates_something_lint_already_reports(seed: Board):
    # `mismatched_coa` leads nowhere. That is missing, not decided, so lint owns
    # it and no decision may mention it.
    blocking = {f"{f.anchor}:{f.field}" for f in rules.blocking(seed)}
    assert "primitive:coas_valid:outcomes" in blocking
    assert not any("mismatched_coa" in point.claim for point in d.decisions(seed))


def test_an_unreachable_card_is_lint_not_a_decision(sound: Board):
    orphan = ActionPrimitive(
        key="orphan", config=ActionConfig(name="Orphan", effect="noop", is_terminal=True)
    )
    board = sound.model_copy(update={"primitives": (*sound.primitives, orphan)})

    assert "primitive:orphan:incoming" in {f"{f.anchor}:{f.field}" for f in rules.findings(board)}
    assert not any("orphan" in point.key() for point in d.termination(board))


# --- stability -------------------------------------------------------------


def test_keys_are_stable_across_runs(seed: Board):
    assert [p.key() for p in d.decisions(seed)] == [p.key() for p in d.decisions(seed)]


def test_keys_do_not_depend_on_the_order_cards_were_added(seed: Board):
    # A board loaded from a different query order must raise the same questions,
    # or dedup silently stops working.
    shuffled = seed.model_copy(update={"primitives": tuple(reversed(seed.primitives))})
    assert {p.key() for p in d.decisions(shuffled)} == {p.key() for p in d.decisions(seed)}


def test_a_claim_only_ever_names_cards_that_are_on_the_board(seed: Board):
    # The claim is derived, never authored, so a reviewer cannot assert a
    # pattern the board does not contain.
    names = {p.config.name for p in seed.primitives if p.config.name} | {
        p.key for p in seed.primitives
    }
    for point in d.decisions(seed):
        assert any(name in point.claim for name in names) or point.kind == "boundedness"


# --- the sweeps ------------------------------------------------------------


def test_the_seed_board_makes_exactly_seven_decisions(seed: Board):
    assert kinds(d.decisions(seed)) == {
        "sequence": 1,
        "termination": 3,
        "boundedness": 1,
        "entry": 1,
        "coverage": 1,
    }


def test_sequence_finds_the_gate_without_being_told_to_look(seed: Board):
    # Nobody wrote a rule about invoices and COAs. This falls out of asking the
    # same question of every conditional edge between two checks.
    (point,) = d.sequence(seed)
    assert "matching COA" in point.claim
    assert "only runs when" in point.claim
    assert "both run" in point.alternative


def test_a_failure_path_that_never_returns_is_the_interesting_termination(seed: Board):
    by_exception = [p for p in d.termination(seed) if "something is wrong" in p.claim]
    assert len(by_exception) == 2
    assert all("comes back once the problem is fixed" in p.alternative for p in by_exception)


def test_convergence_is_silent_until_two_paths_actually_meet(sound: Board, seed: Board):
    assert d.convergence(seed) == []

    joined = sound.model_copy(
        update={
            "edges": (
                sound.edges[0],
                Edge(key="e2", from_key="looks_ok", to_key="done", on_outcomes=["pass"]),
                Edge(key="e3", from_key="looks_ok", to_key="done", on_outcomes=["fail"]),
            )
        }
    )
    (point,) = d.convergence(joined)
    assert "'pass' and 'fail'" in point.claim


def test_boundedness_is_quiet_once_a_deadline_exists(seed: Board):
    assert len(d.boundedness(seed)) == 1

    event = seed.p("prealert_received")
    bounded = event.model_copy(
        update={
            "config": event.config.model_copy(
                update={
                    "timing": Timing(kind="await", deadline="PT48H"),
                    "outcomes": (Outcome(name="timed_out"),),
                }
            )
        }
    )
    board = seed.model_copy(
        update={"primitives": tuple(bounded if p.key == event.key else p for p in seed.primitives)}
    )
    assert d.boundedness(board) == []


def test_coverage_finds_a_check_a_path_can_skip(seed: Board):
    # Reporting the invoice problem ends the process, so the COA check never
    # runs for that shipment. Which is what the corpus contradicts.
    (point,) = d.coverage(seed)
    assert "without" in point.claim
    assert "matching COA" in point.claim


# --- the answer that makes the board cyclic --------------------------------


def test_answering_the_termination_question_makes_the_graph_cyclic(seed: Board):
    # The process owner says "it comes back once the paperwork is fixed", and
    # the board stops being acyclic. A DAG cannot represent the answer its own
    # reviewer just elicited — which is the whole Section 0 argument, as a test.
    before = [p for p in d.termination(seed) if "Report the COA" in p.claim]
    assert len(before) == 1

    answered = seed.model_copy(
        update={
            "edges": (
                *seed.edges,
                Edge(
                    key="e6",
                    from_key="report_coa_discrepancy",
                    to_key="coas_valid",
                    relation="repeat",
                ),
            )
        }
    )

    assert "coas_valid" in answered.downstream("report_coa_discrepancy")
    assert "report_coa_discrepancy" in answered.downstream("coas_valid")
    assert [p for p in d.termination(answered) if "Report the COA" in p.claim] == []


def test_the_repeat_edge_raises_its_own_question(seed: Board):
    # Answering one question creates the structure the next one matches. That is
    # what makes a second round worth more than a longer first one.
    answered = seed.model_copy(
        update={
            "edges": (
                *seed.edges,
                Edge(
                    key="e6",
                    from_key="report_coa_discrepancy",
                    to_key="coas_valid",
                    relation="repeat",
                ),
            )
        }
    )
    rounds = [p for p in d.boundedness(answered) if p.elements == ("edge:e6",)]
    assert len(rounds) == 1
    assert "any number of times" in rounds[0].claim


# --- a decision needs at least two ways to be answered ---------------------


def test_every_decision_offers_a_real_alternative(seed: Board):
    # A "decision" with no plausible other answer is a statement, not a
    # question, and would waste one of six slots per round.
    for point in d.decisions(seed):
        assert point.alternative
        assert point.alternative != point.claim


def test_a_check_whose_result_changes_nothing_is_not_yet_surfaced(sound: Board):
    # Known gap, recorded deliberately. CAAU4056270 shows a check that records
    # counts and always continues, which is legal and worth questioning — but no
    # sweep asks it yet, and inventing one now would be speculation.
    check = CheckPrimitive(
        key="looks_ok",
        config=CheckConfig(name="Looks ok?", outcomes=[Outcome(name="pass"), Outcome(name="fail")]),
    )
    board = sound.model_copy(
        update={
            "primitives": (sound.primitives[0], sound.primitives[1], check, *sound.primitives[3:]),
            "edges": (
                sound.edges[0],
                Edge(key="e2", from_key="looks_ok", to_key="done"),
            ),
        }
    )
    assert d.sequence(board) == []
