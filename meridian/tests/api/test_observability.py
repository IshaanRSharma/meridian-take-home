"""The gymnasium read, and the one distinction it must never blur.

A column no card fills is **blocked**, not failed. Rendering the two the same
way is the single most misleading thing this screen could do: it makes a
converged agent read as 78%, and it sends somebody to patch a file that cannot
possibly contain the fix. So the tests here are built so that an implementation
which dropped `reachable` would give a different answer.

The other half is `_beliefs`, which retires an assumption only when a repair
*named* it. The trap there is substring matching: `status_column_has_no_source`
contains no other id, but a naive `in` against a blob of prose can still fire on
a coincidence, so the cases below include one assumption whose id is a prefix of
another's.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from meridian.api.routes import observability
from meridian.domain.build import Build
from meridian.healing.gym import Cell, Scoreboard


def a_check(seq: int, key: str, total: int, passed: int, failed: int) -> dict[str, Any]:
    """One trace step carrying counts — which is what makes it a check."""
    return {
        "seq": seq,
        "step": key,
        "status": "ok",
        "output": {"total": total, "passed": passed, "failed": failed, "outcome": "pass"},
        "error": None,
    }


def an_action(seq: int, key: str) -> dict[str, Any]:
    """A step with no counts. Not a check, and must not be judged as one."""
    return {"seq": seq, "step": key, "status": "ok", "output": {"ok": True}, "error": None}


def held(gates: list[dict[str, Any]], name: str) -> bool:
    return bool(next(gate["held"] for gate in gates if gate["name"] == name))


def a_build() -> Build:
    return Build(
        spec_id=uuid4(),
        iteration=8,
        source_ref="agents/toy_prealert@7bee6d2",
        created_by="repair",
        entry_point="src/harness.py",
    )


def an_agent(root: Path, assumptions: list[dict[str, Any]]) -> Path:
    """A repository with one agent directory, as `_beliefs` expects to find it."""
    where = root / "agents" / "toy_prealert"
    where.mkdir(parents=True)
    (where / "assumptions.json").write_text(json.dumps({"assumptions": assumptions}))
    return root


ASSUMPTIONS = [
    {
        "id": "batch_matching_is_exact",
        "decision": "compared batch numbers exactly",
        "prompted_by": "primitives.coas_valid.criteria[0].op",
        "because": "the operator has defined semantics",
        "falsified_if": "a certificate is present but spelled differently",
    },
    {
        "id": "batch_matching",
        "decision": "a deliberately shorter id that is a prefix of the one above",
        "prompted_by": None,
        "because": "so a naive substring match would retire the wrong belief",
        "falsified_if": "never",
    },
]


def test_a_belief_is_retired_only_when_a_repair_named_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Naming the assumption is what kills it, not merely failing near it.

    A prediction written in prose cannot be matched against a scoreboard by
    machine. The only honest signal is the repair skill's own instruction to say
    which assumption a patch falsified, so a belief nothing has named stands.
    """
    monkeypatch.setattr(observability, "repo_root", lambda _: an_agent(tmp_path, ASSUMPTIONS))
    recorded = "normalised the suffix; falsified batch_matching_is_exact"

    beliefs = observability._beliefs(a_build(), recorded)

    retired = {one["id"] for one in beliefs if one["falsified"]}
    assert retired == {"batch_matching_is_exact"}


def test_nothing_recorded_leaves_every_belief_standing() -> None:
    """An unexamined assumption is standing, never true.

    Reporting it as held would turn "nobody has tested this" into evidence,
    which is the opposite of what a falsification record is for.
    """
    with_agent = pytest.MonkeyPatch()
    try:
        root = Path(__file__).parent
        with_agent.setattr(observability, "repo_root", lambda _: root)
        assert observability._beliefs(a_build(), "") == []
    finally:
        with_agent.undo()


def test_a_build_that_recorded_no_assumptions_is_worse_not_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing file is an empty list, because the screen still has a curve."""
    (tmp_path / "agents" / "toy_prealert").mkdir(parents=True)
    monkeypatch.setattr(observability, "repo_root", lambda _: tmp_path)
    assert observability._beliefs(a_build(), "anything") == []


def test_a_malformed_assumptions_file_does_not_take_the_screen_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Half-written JSON is a miss. The rest of the training view is unaffected."""
    where = tmp_path / "agents" / "toy_prealert"
    where.mkdir(parents=True)
    (where / "assumptions.json").write_text("{not json")
    monkeypatch.setattr(observability, "repo_root", lambda _: tmp_path)
    assert observability._beliefs(a_build(), "") == []


def test_outside_a_repository_there_are_no_beliefs_to_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`agents/<slug>` is repository-relative, so with no repository there is no path."""
    monkeypatch.setattr(observability, "repo_root", lambda _: None)
    assert observability._beliefs(a_build(), "batch_matching_is_exact") == []


# ── the distinction the heatmap rests on ─────────────────────────────────────


def a_board(reachable: set[str]) -> Scoreboard:
    """One case, three columns, two of them wrong and one of those unfillable."""
    cells = tuple(
        Cell(
            case="CAAU4056270",
            column=column,
            expected=expected,
            actual=actual,
            agreed=expected == actual,
            reachable=column in reachable,
        )
        for column, expected, actual in (
            ("coa_total", 5, 5),
            ("coa_success", 5, 3),
            ("status", "ACTIVE", None),
        )
    )
    return Scoreboard(build=a_build(), cells=cells, splits={"CAAU4056270": "train"}, errored={})


def test_an_unfillable_column_serialises_as_blocked_not_failed() -> None:
    """The one thing this screen may not get wrong.

    `status` is wrong and no card fills it, so no patch can move it. A shaping
    that emitted `fail` here would put a red square where a `--` belongs and
    send the next repair at a file that cannot contain the fix.
    """
    board = a_board({"coa_total", "coa_success"})
    states = {cell.column: cell.state() for cell in board.cells}
    assert states == {"coa_total": "pass", "coa_success": "fail", "status": "blocked"}


def test_the_score_reports_the_ceiling_beside_the_suite() -> None:
    """Two denominators, because they answer different questions.

    2/3 of the suite and 2/2 of what is reachable describe the same build, and
    only the second one is about the code. A curve drawn against the first
    flattens at the ceiling and reads as a stall.
    """
    board = a_board({"coa_total", "coa_success"})
    assert board.score() == (1, 3, 2)
    assert board.blocked() == ("status",)


# ── the trigger test: judging a row with no answer to check it against ───────


def test_a_step_is_a_check_because_it_counts_not_because_of_its_name() -> None:
    """Recognised by carrying a total.

    A name list would need this route edited every time a board grows a check,
    and nobody would find out until a screen quietly under-reported. So an
    action in the trace must not be mistaken for a check that examined nothing.
    """
    gates = observability._gates([an_action(1, "report_it"), a_check(2, "coas_valid", 5, 5, 0)], [])

    assert held(gates, "reached a check")
    assert held(gates, "examined something")


def test_a_trace_with_no_check_at_all_reached_nothing() -> None:
    """Zero checks is not "everything passed" — it is nothing having decided."""
    gates = observability._gates([an_action(1, "report_it")], [])

    assert not held(gates, "reached a check")
    assert not held(gates, "examined something")


def test_a_check_that_examined_zero_rows_agreed_with_nothing() -> None:
    """The trap this gate exists for.

    A check over zero rows reports `pass` and has found nothing to disagree
    with, so a row built from it is all zeros and reads as a clean shipment.
    Counting it as satisfied is how an empty run passes for the wrong reason.
    """
    gates = observability._gates([a_check(1, "coas_valid", 0, 0, 0)], [])

    assert held(gates, "reached a check")
    assert not held(gates, "examined something")


def test_counts_that_do_not_sum_are_caught() -> None:
    """5 examined, 3 passed, 1 failed — one row landed on neither side."""
    gates = observability._gates([a_check(1, "coas_valid", 5, 3, 1)], [])

    assert not held(gates, "counts reconcile")


def test_declined_attachments_are_reported_and_do_not_decide() -> None:
    """Skipped attachments make *what arrived* unreliable, not the sums wrong.

    Letting them vote would fail an otherwise correct row for declining a
    signature image, which is the wrong call and the reason `counts` exists.
    """
    trail = [a_check(1, "coas_valid", 5, 5, 0)]
    gates = observability._gates(trail, [{"source": "image001.gif", "reason": "not a format"}])

    assert not held(gates, "everything was read")
    assert all(gate["held"] for gate in gates if gate["counts"])
    assert [gate["name"] for gate in gates if not gate["counts"]] == ["everything was read"]


def test_a_run_that_could_not_be_keyed_is_a_finding_and_never_a_row() -> None:
    """`needs_correlation` is a result, not a failure.

    The message is a real pre-alert, it was read, and the process model has no
    rule for an air waybill. It carries no gates because there is no row to
    judge — and `trustworthy` is None rather than False, because "not worth
    believing" and "there is nothing here to believe" are different states.
    """
    row = {
        "run_id": uuid4(),
        "iteration": 9,
        "started_at": None,
        "declined": [],
        "output": json.dumps(
            {
                "state": "needs_correlation",
                "subject": "Fwd: FW: Pre-Alerts Documents // EUGIA US LLC",
                "sender": "prealertaurobindo@lynklabs.io",
                "received_at": "2026-08-04T13:18:00Z",
                "attachments": ["530610031850.pdf", "3EL26022 FINAL FP COA.pdf"],
                "reason": "the correlation key is an ISO container code; this names an air waybill",
            }
        ),
    }

    found = observability._triggered(row, [])

    assert found["state"] == "needs_correlation"
    assert found["shipment"] is None
    assert found["trustworthy"] is None
    assert found["gates"] == []
    assert found["finding"]["attachments"] == [
        "530610031850.pdf",
        "3EL26022 FINAL FP COA.pdf",
    ]
    assert "air waybill" in found["finding"]["reason"]


def test_a_processed_run_shows_what_it_produced_beside_why_to_doubt_it() -> None:
    """The row is shown whether or not it is trustworthy.

    Withholding the output of a run nobody can score would leave a verdict and
    no evidence, which is the opposite of what this panel is for. The shipment
    number is how the row was filed rather than something a check computed, so
    it is the heading and not one of the values.
    """
    row = {
        "run_id": uuid4(),
        "iteration": 9,
        "started_at": None,
        "declined": [],
        "output": json.dumps({"shipment_no": "MMAU1407799", "coa_total": 4, "coa_success": 4}),
    }

    found = observability._triggered(row, [a_check(1, "coas_valid", 4, 4, 0)])

    assert found["state"] == "processed"
    assert found["shipment"] == "MMAU1407799"
    assert found["row"] == {"coa_total": 4, "coa_success": 4}
    assert found["trustworthy"] is True


def test_a_count_that_is_not_a_number_does_not_crash_the_screen() -> None:
    """A trace is stored jsonb and a malformed step must not take the panel down."""
    trail = [
        {"seq": 1, "step": "coas_valid", "status": "ok", "output": {"total": None}, "error": None}
    ]

    gates = observability._gates(trail, [])

    assert held(gates, "reached a check")
    assert not held(gates, "examined something")
