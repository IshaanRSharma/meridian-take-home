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
