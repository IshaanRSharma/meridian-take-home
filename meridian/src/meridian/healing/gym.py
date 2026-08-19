"""The healing loop as an environment, so a run of it can be scored.

The loop already had an oracle and no scoreboard. `eval sweep` answers *did this
build pass*, which is the wrong grain for the question everybody actually asks:
**is the agent converging, and how much did each case cost?** That needs the
whole episode — every build, every patch, and where each case first went green —
and all of it is already stored. Nothing here runs anything new; it reads `runs`
and `repairs` and arranges them.

The environment framing is not decoration. It names the four things a repair
loop has and a sweep does not:

```
observation   the scoreboard: every (case, column), and why each one is wrong
action        a patch — applied OUTSIDE this module, by a person or a skill
reward        columns gained, minus columns regressed
terminated    every REACHABLE column is green
```

`action` sitting outside is the design, not a limitation. Repair is human-run
here, so an environment that also chose the patch would be a different product;
this one measures the loop somebody else is turning.

**Reachable is the load-bearing word.** A column no Check `fills` cannot be
produced by any patch, so counting it against the agent makes a converged loop
look broken and sends the next iteration at a number it can never move. The
ceiling is the line between *the code's problem* and *the board's problem*, and
scoring against the wrong one is how a repair loop burns its budget.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from meridian.domain.build import Build, EvalCase, RunResult
from meridian.domain.frozen import FrozenSpec
from meridian.healing.compare import compare
from meridian.repositories import builds as builds_repo
from meridian.repositories import evals as evals_repo


def reachable_columns(spec: FrozenSpec) -> frozenset[str]:
    """Every column some Check on this board claims to write.

    A `Fill` is the only thing that puts a number in the output row, so the set
    of fills is exactly the set of columns a perfect agent could produce. What
    is left is not a bug — it is a card nobody drew.
    """
    return frozenset(
        fill["field"]["path"] if isinstance(fill, dict) else fill.field.path
        for card in spec.primitives.values()
        for fill in (getattr(card.config, "fills", None) or [])
    )


@dataclass(frozen=True)
class Cell:
    """One (case, column) measurement."""

    case: str
    column: str
    expected: object
    actual: object
    agreed: bool
    reachable: bool

    def nearness(self) -> float:
        """How close this cell came, between 0 and 1.

        Exact agreement is the oracle and nothing here changes it — `agreed` is
        still the only thing that scores. This is the diagnostic beside it,
        because a binary reward cannot tell 13-of-14 from 0-of-14 and those are
        completely different states of the same agent. A loop ranked on exact
        match alone is blind to the difference between nearly right and nowhere
        near, which is exactly the signal "is it converging" needs.

        Non-numeric columns have no meaningful distance — `ACTIVE` is not closer
        to `RESOLVED` than anything else is — so they stay binary.
        """
        if self.agreed:
            return 1.0
        want, got = self.expected, self.actual
        if isinstance(want, bool) or isinstance(got, bool):
            return 0.0
        if not isinstance(want, int | float) or not isinstance(got, int | float):
            return 0.0
        return max(0.0, 1.0 - abs(got - want) / max(abs(want), 1))

    def state(self) -> str:
        """Three states, because two would hide the ceiling.

        `blocked` is not a failure of the code. Rendering it as one is what makes
        a converged agent read as 78% and sends somebody to patch a file that
        cannot possibly contain the fix.
        """
        if self.agreed:
            return "pass"
        return "fail" if self.reachable else "blocked"


@dataclass(frozen=True)
class Scoreboard:
    """One build, measured — the environment's observation.

    Every rate is reported twice, against the suite and against the ceiling,
    because the two answer different questions and only the second one is about
    the code. A build at 7/9 and a build at 7/7 are the same agent.
    """

    build: Build
    cells: tuple[Cell, ...]
    splits: Mapping[str, str]
    errored: Mapping[str, str]

    def cases(self) -> tuple[str, ...]:
        """Case keys, in a stable order."""
        return tuple(dict.fromkeys(cell.case for cell in self.cells))

    def columns(self) -> tuple[str, ...]:
        """Column names, in a stable order."""
        return tuple(dict.fromkeys(cell.column for cell in self.cells))

    def within(self, split: str | None = None) -> tuple[Cell, ...]:
        """Cells belonging to one split, or all of them."""
        if split is None:
            return self.cells
        return tuple(c for c in self.cells if self.splits.get(c.case) == split)

    def score(self, split: str | None = None) -> tuple[int, int, int]:
        """Columns agreed, columns measured, columns reachable.

        Three numbers rather than two: the middle one is what the suite asked
        for and the third is the most any code could have answered.
        """
        cells = self.within(split)
        return (
            sum(1 for c in cells if c.agreed),
            len(cells),
            sum(1 for c in cells if c.reachable),
        )

    def green(self, split: str | None = None) -> tuple[str, ...]:
        """Cases where every reachable column agrees.

        Reachable, not every — a case is as green as the board allows it to be,
        and holding it to a column the board never claimed to fill would mean no
        case ever passes and the loop never terminates.
        """
        by_case: dict[str, bool] = {}
        for cell in self.within(split):
            if cell.reachable:
                by_case[cell.case] = by_case.get(cell.case, True) and cell.agreed
        return tuple(case for case, ok in by_case.items() if ok)

    def nearness(self, split: str | None = None) -> float:
        """Mean closeness over the columns a card could actually fill.

        Reported beside the score, never instead of it. Two builds both scoring
        9/63 are not the same build if one is off by one everywhere and the
        other returns nothing, and this is the only number that separates them.
        """
        cells = [c for c in self.within(split) if c.reachable]
        return sum(c.nearness() for c in cells) / len(cells) if cells else 0.0

    def blocked(self) -> tuple[str, ...]:
        """Columns no card fills, so no patch can ever produce them."""
        return tuple(sorted({c.column for c in self.cells if not c.reachable}))

    def terminated(self, split: str | None = None) -> bool:
        """Whether this build is done — every reachable column green."""
        agreed, _, reachable = self.score(split)
        return reachable > 0 and agreed >= reachable


@dataclass(frozen=True)
class Episode:
    """A whole healing run: every build, in order, and what each one cost.

    This is the artefact the loop never produced. A curve of builds says whether
    the agent improved; `steps_to_green` says **how many iterations one case
    took**, which is the number that separates a loop that diagnoses from one
    that guesses.
    """

    boards: tuple[Scoreboard, ...]
    attempts: Mapping[str, tuple[str, ...]]

    def curve(self) -> tuple[tuple[int, int, int, int, int], ...]:
        """Per build: iteration, columns agreed, measured, reachable, CASES.

        The case count is carried because without it the curve invites a
        comparison that is not valid. Builds are swept on whatever set somebody
        ran at the time, so a build measured on two cases and one measured on
        ten produce numbers with different denominators — and plotted together
        they read as a collapse or a leap that never happened. The gate already
        refuses to judge across different case sets; the curve has to at least
        show it.
        """
        return tuple(
            (b.build.iteration, *b.score(), len(b.cases())) for b in self.boards
        )

    def steps_to_green(self) -> dict[str, int | None]:
        """The build iteration where each case first passed every reachable column.

        `None` means it never did. Reported rather than omitted: a case that
        never went green is the most important row in the table, and dropping it
        would make the average look like convergence.
        """
        first: dict[str, int | None] = {}
        for board in self.boards:
            for case in board.cases():
                first.setdefault(case, None)
            for case in board.green():
                if first.get(case) is None:
                    first[case] = board.build.iteration
        return first

    def resisted(self, floor: int = 3) -> tuple[str, ...]:
        """Signatures attempted `floor` times or more without being accepted.

        The repair skill calls three attempts on one signature a misdiagnosis
        rather than persistence. Naming them is what turns that rule from advice
        into something the loop reports on itself.
        """
        return tuple(
            sorted(
                signature
                for signature, outcomes in self.attempts.items()
                if len(outcomes) >= floor and "accepted" not in outcomes
            )
        )


async def observe(
    connection: asyncpg.Connection, *, build: Build, spec: FrozenSpec, spec_id: UUID
) -> Scoreboard:
    """Score one build from what is already stored.

    Reads rather than runs. Both halves have been measured already, so the
    scoreboard costs a query — and a version that re-swept would be measuring
    code that has since been edited, which is a different question wearing the
    same shape.
    """
    results = await evals_repo.results_for(connection, build.identity)
    cases = await evals_repo.cases_for(connection, spec_id)
    return _score(build, results, cases, reachable_columns(spec))


def _score(
    build: Build,
    results: Sequence[RunResult],
    cases: Sequence[EvalCase],
    reachable: frozenset[str],
) -> Scoreboard:
    splits = {case.key: case.split for case in cases}
    cells: list[Cell] = []
    errored: dict[str, str] = {}

    for result in results:
        comparison = compare(
            result.case_key, result.expected_output, result.output, errored=result.errored
        )
        if comparison.errored:
            errored[result.case_key] = comparison.errored
        for column in comparison.matched:
            cells.append(
                Cell(
                    case=result.case_key,
                    column=column,
                    expected=result.expected_output.get(column),
                    actual=result.output.get(column),
                    agreed=True,
                    reachable=column in reachable,
                )
            )
        for mismatch in comparison.mismatched:
            cells.append(
                Cell(
                    case=result.case_key,
                    column=mismatch.column,
                    expected=mismatch.expected,
                    actual=mismatch.actual,
                    agreed=False,
                    reachable=mismatch.column in reachable,
                )
            )

    cells.sort(key=lambda c: (c.case, c.column))
    return Scoreboard(build=build, cells=tuple(cells), splits=splits, errored=errored)


async def episode(
    connection: asyncpg.Connection, *, spec: FrozenSpec, spec_id: UUID
) -> Episode:
    """Every build against this spec, scored, plus what each patch was aimed at.

    A build nobody swept contributes nothing and is skipped rather than shown as
    a zero: an unmeasured build is an absence, and plotting it as the origin
    would draw a crash into the curve that never happened.
    """
    boards = []
    for build in await builds_repo.for_spec(connection, spec_id):
        board = await observe(connection, build=build, spec=spec, spec_id=spec_id)
        if board.cells:
            boards.append(board)

    rows = await connection.fetch(
        "select r.failure_signature, r.status from repairs r "
        "join agent_builds b on b.id = r.build_id where b.spec_id = $1 "
        "order by r.created_at",
        spec_id,
    )
    attempts: dict[str, list[str]] = {}
    for row in rows:
        attempts.setdefault(row["failure_signature"], []).append(row["status"])

    return Episode(
        boards=tuple(boards),
        attempts={k: tuple(v) for k, v in attempts.items()},
    )
