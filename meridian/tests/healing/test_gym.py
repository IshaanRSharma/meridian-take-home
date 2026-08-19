"""Scoring a build, and scoring the loop that produced it.

Every test here is built so that the **obvious wrong implementation gives a
different answer**. The obvious wrong implementation is the one that counts a
column nothing fills as a failure — it makes a converged agent read as 78%,
never terminates, and sends the next iteration at a number no patch can move.
So the fixture deliberately measures a column outside the ceiling, and a scorer
that ignored `reachable` would disagree with every assertion below.
"""

from __future__ import annotations

from uuid import uuid4

from meridian.domain.build import Build, EvalCase, RunResult
from meridian.healing.gym import Episode, Scoreboard, _score, reachable_columns

from .conftest import a_spec

# `a_spec` fills three columns. `status` is measured by the suite and filled by
# nothing, which is exactly the pre-alert board's real situation.
FILLED = {"coa_total": 5, "coa_success": 5, "failed_coa": 0}
MEASURED = FILLED | {"status": "RESOLVED"}


def a_build(iteration: int = 1) -> Build:
    return Build(
        spec_id=uuid4(),
        iteration=iteration,
        source_ref=f"agents/toy_prealert@sha{iteration}",
        created_by="repair",
        entry_point="src/entry.py",
    )


def a_case(key: str = "CAAU4056270", split: str = "train") -> EvalCase:
    return EvalCase(
        key=key,
        split=split,
        origin="authored",
        input={"shipment_no": key},
        expected_output=MEASURED,
    )


def a_result(key: str, output: dict[str, object], errored: str | None = None) -> RunResult:
    return RunResult(
        run_id=uuid4(),
        case_id=uuid4(),
        case_key=key,
        outcome="error" if errored else "failed",
        output=output,
        expected_output=MEASURED,
        errored=errored,
    )


def board_for(output: dict[str, object], *, iteration: int = 1, errored: str | None = None):
    return _score(
        a_build(iteration),
        [a_result("CAAU4056270", output, errored)],
        [a_case()],
        reachable_columns(a_spec()),
    )


def test_reachable_is_what_some_check_fills() -> None:
    """The ceiling comes off `fills`, not off what the suite happens to measure."""
    assert reachable_columns(a_spec()) == frozenset(FILLED)


def test_a_column_nothing_fills_is_blocked_not_failed() -> None:
    """`status` is wrong and it is not the code's fault.

    A scorer without the ceiling calls this a failure, reports 3/4, and never
    terminates. The distinction is the whole point of the module.
    """
    board = board_for(FILLED)
    states = {(c.column, c.state()) for c in board.cells}
    assert ("status", "blocked") in states
    assert all(state == "pass" for column, state in states if column != "status")


def test_a_build_that_fills_every_reachable_column_has_terminated() -> None:
    """Green means as green as the board allows, or the loop can never stop."""
    board = board_for(FILLED)
    assert board.terminated()
    assert board.green() == ("CAAU4056270",)
    assert board.score() == (3, 4, 3)


def test_a_reachable_column_that_disagrees_is_a_real_failure() -> None:
    """The ceiling must not swallow the failures repair exists to fix."""
    board = board_for(FILLED | {"coa_success": 2})
    assert not board.terminated()
    assert board.green() == ()
    assert board.score() == (2, 4, 3)


def test_an_errored_case_fails_every_column_it_was_measured_on() -> None:
    """Not zero columns.

    Summing over what came back would score a case that never ran as a perfect
    row of nothing, which is how a crash reads as convergence.
    """
    board = board_for({}, errored="KeyError: 'batch_no'")
    assert board.score() == (0, 4, 3)
    assert board.errored["CAAU4056270"].startswith("KeyError")


def test_score_is_reported_per_split() -> None:
    """A split nobody measured contributes nothing rather than a zero."""
    board = _score(
        a_build(),
        [a_result("CAAU4056270", FILLED), a_result("MCAU6047165", FILLED | {"failed_coa": 9})],
        [a_case("CAAU4056270", "train"), a_case("MCAU6047165", "holdout")],
        reachable_columns(a_spec()),
    )
    assert board.score("train") == (3, 4, 3)
    assert board.score("holdout") == (2, 4, 3)
    assert board.green("train") == ("CAAU4056270",)
    assert board.green("holdout") == ()


def test_steps_to_green_names_the_first_build_that_passed() -> None:
    """The first, not the last.

    An implementation taking the latest green build would report the end of the
    episode for every case and make the cost of each one look identical.
    """
    run = Episode(
        boards=(
            board_for(FILLED | {"coa_success": 1}, iteration=1),
            board_for(FILLED, iteration=2),
            board_for(FILLED, iteration=3),
        ),
        attempts={},
    )
    assert run.steps_to_green() == {"CAAU4056270": 2}


def test_a_case_that_never_passed_is_reported_as_never() -> None:
    """Dropping it would make the average look like convergence."""
    run = Episode(boards=(board_for(FILLED | {"coa_total": 0}, iteration=1),), attempts={})
    assert run.steps_to_green() == {"CAAU4056270": None}


def test_resisted_names_signatures_attempted_three_times_and_never_accepted() -> None:
    """The repair skill's own stop rule, reported by the loop about itself."""
    run = Episode(
        boards=(),
        attempts={
            "coas_valid :: output_diff :: coa_success": ("regressed", "regressed", "regressed"),
            "invoice_complete :: output_diff :: goods_failed": ("regressed", "accepted"),
            "coas_valid :: output_diff :: coa_total": ("regressed",),
        },
    )
    assert run.resisted() == ("coas_valid :: output_diff :: coa_success",)


def test_curve_reports_reachable_and_the_case_count() -> None:
    """A curve against the suite would flatten at the ceiling and read as a stall.

    The case count rides along because two builds swept on different sets have
    different denominators, and plotted side by side they show a collapse that
    never happened.
    """
    run = Episode(
        boards=(
            board_for(FILLED | {"coa_success": 1, "coa_total": 0}, iteration=1),
            board_for(FILLED, iteration=2),
        ),
        attempts={},
    )
    assert run.curve() == ((1, 1, 4, 3, 1), (2, 3, 4, 3, 1))


def test_a_scoreboard_with_no_runs_is_empty_rather_than_perfect() -> None:
    """An unswept build must never read as terminated: zero of zero is not done."""
    board = Scoreboard(build=a_build(), cells=(), splits={}, errored={})
    assert not board.terminated()
    assert board.green() == ()
