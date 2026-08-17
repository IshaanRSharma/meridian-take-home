"""Tests for walking a scenario over the board, and for citing the walk.

The walk itself belongs to `Board.dry_run`, so what is tested here is the two
things the reviewer needs from it that the board does not provide: that a
scenario it cannot run reports rather than raises, and that the citation it
attaches to a question can be run again by someone who doubts it.

The second is the load-bearing one. A structural claim whose evidence does not
reproduce gets dropped, so `detail` has to survive being written to jsonb and
read back and still produce the same answer.
"""

import json
from typing import cast

from meridian.domain.graph import Board
from meridian.domain.review import Scenario
from meridian.reviewer import dryrun, scenarios


def enumerated(board: Board, key: str) -> Scenario:
    return next(sc for sc in scenarios.enumerate_from(board) if sc.key == key)


# --- what the enumerated scenarios find on the seed board ------------------


def test_the_clean_run_reaches_an_ending(seed: Board):
    happy = scenarios.enumerate_from(seed)[0]
    result = dryrun.walk(seed, happy)

    assert result.result == "reached_terminal"
    assert result.trace[-1].key == "documentation_validated"


def test_a_step_the_drawing_never_continues_from_is_a_dead_end(seed: Board):
    # Reporting the COA problem is where the SOP stops. Nobody said the process
    # does, and the difference between the two is this result.
    result = dryrun.walk(seed, enumerated(seed, "coas_valid_missing_coa"))

    assert result.result == "dead_end"
    assert result.trace[-1].key == "report_coa_discrepancy"


def test_an_outcome_with_no_line_out_of_it_is_an_undefined_branch(seed: Board):
    result = dryrun.walk(seed, enumerated(seed, "coas_valid_mismatched_coa"))

    assert result.result == "undefined_branch"
    assert "mismatched_coa" in (result.trace[-1].note or "")


def test_a_failing_check_hides_everything_downstream_of_it(seed: Board):
    # An invoice problem ends the process, so the COA check never runs for that
    # shipment. Nobody predicted that; it falls out of the walk.
    result = dryrun.walk(seed, enumerated(seed, "invoice_complete_missing_information"))

    assert result.result == "dead_end"
    assert "coas_valid" in result.unreached


# --- a scenario the board cannot run ---------------------------------------


def test_a_scenario_with_nowhere_to_start_is_reported_rather_than_raised(two_entries: Board):
    # One unrunnable scenario would otherwise take the whole round with it.
    stranded = Scenario(
        key="stranded", kind="probe", description="Something arrives.", outcomes={}, start=None
    )

    result = dryrun.walk(two_entries, stranded)

    assert result.result == "dead_end"
    assert result.trace == ()
    assert result.unreached == tuple(node.key for node in two_entries.nodes())


def test_naming_the_way_in_makes_a_board_with_two_of_them_walkable(two_entries: Board):
    happy = scenarios.enumerate_from(two_entries)[0]

    assert dryrun.walk(two_entries, happy).result == "reached_terminal"


# --- the citation ----------------------------------------------------------


def test_a_citation_names_the_call_that_produced_it(seed: Board):
    happy = scenarios.enumerate_from(seed)[0]
    evidence = dryrun.evidence_for(happy, dryrun.walk(seed, happy))

    assert evidence.tool == "dry_run"
    assert evidence.result == "reached_terminal"
    assert evidence.detail == {
        "scenario": "happy_path",
        "start": None,
        "outcomes": {"invoice_complete": "pass", "coas_valid": "pass"},
        "trace": [
            "prealert_received",
            "invoice_complete",
            "coas_valid",
            "documentation_validated",
        ],
    }


def test_every_citation_re_runs_to_the_result_it_claims(seed: Board):
    for scenario in scenarios.enumerate_from(seed):
        evidence = dryrun.evidence_for(scenario, dryrun.walk(seed, scenario))
        detail = evidence.detail or {}

        again = seed.dry_run(
            cast(dict[str, str | tuple[str, ...]], detail["outcomes"]),
            cast(str | None, detail["start"]),
        )
        assert again.result == evidence.result, scenario.key


def test_a_citation_still_re_runs_after_a_trip_through_the_database(seed: Board):
    # An answer that changes between visits is how a resubmission is described,
    # and jsonb has no tuples. The citation has to come back runnable.
    resubmitted = Scenario(
        key="coa_corrected",
        kind="probe",
        description="A corrected certificate arrives after the problem is reported.",
        outcomes={"invoice_complete": "pass", "coas_valid": ("missing_coa", "pass")},
    )
    evidence = dryrun.evidence_for(resubmitted, dryrun.walk(seed, resubmitted))

    assert evidence.detail is not None
    assert evidence.detail["outcomes"] == {
        "invoice_complete": "pass",
        "coas_valid": ["missing_coa", "pass"],
    }

    stored = json.loads(json.dumps(evidence.detail))
    again = seed.dry_run(stored["outcomes"], stored["start"])
    assert again.result == evidence.result


# --- describing a walk to a model --------------------------------------------
#
# A walk is a list of step keys and that is not what anyone can reason about.
# What makes a semantic question possible is the shape: the arrows named, what
# each step on the path actually reads, and what the walk never got to.


def test_every_enumerated_situation_is_offered_to_the_model(seed: Board):
    # A situation the reviewer walked but never showed the model is work thrown
    # away — and the undefined branch is exactly the one that would be lost.
    walked = [(s, dryrun.walk(seed, s)) for s in scenarios.enumerate_from(seed)]
    described = dryrun.describe(seed, walked)

    for scenario in scenarios.enumerate_from(seed):
        assert scenario.key in described


def test_a_situation_is_described_by_where_it_ended_and_what_it_missed(seed: Board):
    walked = scenarios.enumerate_from(seed)
    failing = next(s for s in walked if s.key.startswith("invoice_complete_"))
    described = dryrun.describe(seed, [(failing, dryrun.walk(seed, failing))])

    assert "dead_end" in described
    # "a failing invoice means the COA check never runs" is only visible here.
    assert "coas_valid" in described


def test_a_situation_with_nothing_missed_says_so_cleanly(seed: Board):
    happy = next(s for s in scenarios.enumerate_from(seed) if s.kind == "happy")
    assert isinstance(dryrun.describe(seed, [(happy, dryrun.walk(seed, happy))]), str)


def test_the_scenario_type_is_what_gets_described(seed: Board):
    assert all(isinstance(s, Scenario) for s in scenarios.enumerate_from(seed))


def test_a_situation_is_described_as_a_path_not_a_list_of_steps(seed: Board):
    # A semantic question is almost always about a relationship between two
    # points on a path — how do you match this to that, by the time you get
    # here. Handed an adjacency list, the model has to rebuild the shape first
    # and can get it wrong; handed the path, it reasons about it directly.
    walked = [(s, dryrun.walk(seed, s)) for s in scenarios.enumerate_from(seed)]
    described = dryrun.describe(seed, walked)

    assert "──e2 on pass──▶" in described
    assert "(exception)" in described


def test_a_path_says_what_each_step_does_with_the_data(seed: Board):
    walked = [(s, dryrun.walk(seed, s)) for s in scenarios.enumerate_from(seed)]
    described = dryrun.describe(seed, walked)

    assert "brings in" in described
    assert "commercial_invoice.line_items[].batch_no" in described


def test_what_a_step_tests_is_kept_apart_from_what_it_reports(seed: Board):
    # `invoice_complete` tests four identifiers and *reports* the invoice number
    # and the drug description as proof. Rolled together, the model asks why the
    # invoice number has no rule — a question about a field that was never meant
    # to have one.
    walked = [(s, dryrun.walk(seed, s)) for s in scenarios.enumerate_from(seed)]
    described = dryrun.describe(seed, walked)

    tested = described.split("tests")[1].split("\n")[0]
    assert "hts_number" in tested
    assert "invoice_no" not in tested
    assert "reports" in described
