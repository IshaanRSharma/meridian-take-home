"""Run every eval case through a generated agent, and write down what happened.

The sweep is generic and the agent is not, so the split is: **the agent runs
itself, the sweep loads it, compares it and stores it.** `build.json` names an
entry point exposing one coroutine, `run_case(case) -> CaseOutcome`, and
everything Temporal — starting an environment, registering a worker, sending
signals — happens inside it, where the process's own vocabulary is in scope. A
harness that knew about `Arrival` and `documents_arrived` would be a harness for
one process.

**Comparison is per column.** A patch that fixes one and breaks another leaves
the row failing before and after, so a row-level sweep watches a regression go
past. See `compare`.

**One transaction for the whole sweep.** A partial sweep is worse than none: the
gate compares two builds case by case, and a run that stopped after three of
five would be scored as a complete measurement that happened to be missing rows.
A case that raises is caught and recorded, so only an infrastructure failure
aborts — and re-running one is exactly right.

**Every case is wrapped in a timeout.** An exception in Temporal workflow code
is a workflow task failure, which retries forever: no traceback, no exit. Left
alone a single bad case hangs the sweep with no output at all.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

import asyncpg

from meridian import events
from meridian.domain.build import Build, EvalCase
from meridian.domain.errors import MeridianError
from meridian.domain.frozen import FrozenSpec
from meridian.domain.primitives import BOARD_KEY
from meridian.healing.compare import Comparison, compare, is_empty_sweep, score
from meridian.healing.localize import Located, locate, locate_error
from meridian.repositories import evals as evals_repo
from meridian.runtime.harness import CaseOutcome

CASE_TIMEOUT_SECONDS = 180.0
_IS_CARD_KEY = re.compile(BOARD_KEY)


class AgentNotRunnableError(MeridianError):
    """The build names an entry point that cannot be loaded, so nothing ran.

    Distinct from a case that failed, and deliberately fatal. Reporting one
    import error per case would render an agent that never started as five
    identical per-case bugs, which sends somebody to read the cases.
    """


class RunCase(Protocol):
    """The one thing a generated agent's entry point has to expose."""

    def __call__(self, case: Mapping[str, Any]) -> Awaitable[CaseOutcome]:
        """Run one eval case to completion and report what it produced."""
        ...


@dataclass(frozen=True)
class Swept:
    """One sweep, scored.

    `empty` is carried beside the score rather than folded into it, because the
    two disagree in the case that matters: a run returning all zeros scores every
    column expecting zero as a pass, so the number can read green while nothing
    ever reached a check.
    """

    build_id: UUID
    results: tuple[Comparison, ...]
    passed: int
    total: int
    empty: bool
    rejected_steps: tuple[str, ...] = ()

    def errored(self) -> tuple[Comparison, ...]:
        """Cases that raised rather than disagreeing."""
        return tuple(r for r in self.results if r.errored)


async def sweep(  # noqa: PLR0913 - a sweep names what it runs, against what, for whom
    connection: asyncpg.Connection,
    *,
    build: Build,
    spec: FrozenSpec,
    cases: Sequence[EvalCase],
    agents_root: Path,
    cycle_id: UUID,
    mode: evals_repo.Mode = "sandbox",
    case_timeout: float = CASE_TIMEOUT_SECONDS,
) -> Swept:
    """Run the suite against one build and record every case."""
    await evals_repo.clear_runs(connection, build.identity)

    results: list[Comparison] = []
    rejected: list[str] = []
    with agent_loaded(agents_root, build) as run_case:
        async with events.during(
            connection, cycle_id=cycle_id, phase="eval", kind="sweep", build_id=build.identity
        ) as closing:
            for case in cases:
                result = await _one(
                    connection,
                    run_case=run_case,
                    build=build,
                    spec=spec,
                    case=case,
                    cycle_id=cycle_id,
                    mode=mode,
                    case_timeout=case_timeout,
                    rejected=rejected,
                )
                results.append(result)

            passed, total = score(results)
            closing.update(passed=passed, total=total, cases=len(results))

    return Swept(
        build_id=build.identity,
        results=tuple(results),
        passed=passed,
        total=total,
        empty=is_empty_sweep(results),
        rejected_steps=tuple(dict.fromkeys(rejected)),
    )


async def _one(  # noqa: PLR0913 - one case needs everything the sweep was given
    connection: asyncpg.Connection,
    *,
    run_case: RunCase,
    build: Build,
    spec: FrozenSpec,
    case: EvalCase,
    cycle_id: UUID,
    mode: evals_repo.Mode,
    case_timeout: float,
    rejected: list[str],
) -> Comparison:
    # Emitted before the case runs, not after. A live case takes as long as a
    # mailbox and a model take, and with only a completion event a watcher sees
    # nothing at all until the first one lands — which is indistinguishable from
    # a sweep that has wedged. This is what makes a progress view a progress
    # view rather than a slowly-filling results table.
    await events.emit(
        connection,
        cycle_id=cycle_id,
        phase="eval",
        kind="case",
        status="started",
        build_id=build.identity,
        case_key=case.key,
    )

    outcome, errored = await _execute(run_case, case, case_timeout)
    result = compare(case.key, case.expected_output, outcome.output, errored=errored)

    run_id = await evals_repo.save_run(
        connection,
        build_id=build.identity,
        case_id=case.id,
        mode=mode,
        outcome="error" if errored else ("passed" if result.passed() else "failed"),
        output=outcome.output,
        steps=_steps(outcome, rejected),
        declined=[gone.model_dump(mode="json") for gone in outcome.declined],
        errored=errored,
    )
    for found in _detections(result, outcome, spec, build.file_map):
        await evals_repo.save_failure(
            connection,
            run_id,
            signature=found.signature,
            detector=found.detector,
            primitive_key=found.primitive_key,
            detail=found.detail | ({"file": found.file} if found.file else {}),
        )

    await events.emit(
        connection,
        cycle_id=cycle_id,
        phase="eval",
        kind="case",
        status="ok" if result.passed() else "failed",
        build_id=build.identity,
        run_id=run_id,
        case_key=case.key,
        detail={"mismatched": [m.column for m in result.mismatched], "errored": errored},
    )
    return result


async def _execute(
    run_case: RunCase, case: EvalCase, case_timeout: float
) -> tuple[CaseOutcome, str | None]:
    """One case, or the reason there is no case.

    Every exception is a result rather than an escape. The one that raised is
    frequently the least interesting of five, and letting it propagate would
    throw away the four that ran.
    """
    try:
        return await asyncio.wait_for(run_case(case.input), timeout=case_timeout), None
    except TimeoutError:
        return CaseOutcome(), f"TimeoutError: no result within {case_timeout:.0f}s"
    except Exception as error:
        # An errored case is a result. Letting it escape would discard every case
        # after it, and the one that raised is often the least interesting.
        return CaseOutcome(), f"{type(error).__name__}: {error}"


def _detections(
    result: Comparison,
    outcome: CaseOutcome,
    spec: FrozenSpec,
    file_map: Mapping[str, str],
) -> list[Located]:
    """What to record about one case's failure.

    An errored case gets **one** row, not one per column: every column is wrong
    for the same single reason, and splitting that across buckets would bury the
    reason and inflate every count the loop ranks work by.
    """
    if result.errored:
        return [locate_error(result.errored, outcome.steps, spec, file_map)]
    return [locate(m, spec, file_map) for m in result.mismatched]


def _steps(outcome: CaseOutcome, rejected: list[str]) -> list[dict[str, Any]]:
    """The trace as `run_steps` rows, with `name` renamed to `primitive_key`.

    `primitive_key` is the board_key domain, so a step called "Check COAs" fails
    the insert. Losing a whole sweep over a trace problem would discard eval
    results that are perfectly good, and slugifying the name would write a key
    into a column everything else joins on that names no card. So the step is
    dropped and reported, and the score survives.
    """
    rows = []
    for step in outcome.steps:
        if not _IS_CARD_KEY.match(step.name):
            rejected.append(step.name)
            continue
        dumped = step.model_dump(mode="json")
        rows.append({k: v for k, v in dumped.items() if k != "name"} | {"primitive_key": step.name})
    return rows


@contextmanager
def agent_loaded(agents_root: Path, build: Build) -> Iterator[RunCase]:
    """Make the agent importable, hand back its `run_case`, then undo it.

    A context manager rather than a function, and the reason is Temporal's
    sandbox. It **re-imports the module a workflow is defined in, by name,
    through the ordinary finders**, on every workflow task — so executing the
    file and stashing the result in `sys.modules` is not enough. The directory
    has to stay on `sys.path` for as long as workflows are running, and the
    module has to be named after its own file so a finder can locate it there.

    That rules out naming the module after the build, which is why the exit
    matters: two builds of one agent are two different programs under one name,
    and leaving the first cached would silently sweep it twice. Nothing is left
    behind, so a second sweep in the same process reads the file again.

    The entry point's own directory is what goes on the path, so an agent laid
    out the way the generator is told to lay one out — `src/workflow.py`
    importing `checks/coas_valid.py` — imports itself the way it expects to.
    """
    if not build.entry_point:
        raise AgentNotRunnableError(
            f"build {build.iteration} names no entry point — "
            "build.json must carry one, and `meridian build register` reads it"
        )

    path = agents_root / build.slug() / build.entry_point
    if not path.is_file():
        raise AgentNotRunnableError(f"{path} does not exist")

    name, folder = path.stem, str(path.parent)
    sys.path.insert(0, folder)
    # An earlier build of this agent, or an unrelated module of the same name.
    # Either would be returned by `import_module` in preference to the file this
    # build actually points at.
    displaced = sys.modules.pop(name, None)
    try:
        yield _run_case(name, build)
    finally:
        sys.modules.pop(name, None)
        if displaced is not None:
            sys.modules[name] = displaced
        if folder in sys.path:
            sys.path.remove(folder)


def _run_case(name: str, build: Build) -> RunCase:
    try:
        module = importlib.import_module(name)
    except Exception as error:
        raise AgentNotRunnableError(f"{build.entry_point} would not import: {error}") from error

    found: Callable[..., Any] | None = getattr(module, "run_case", None)
    if found is None or not callable(found):
        raise AgentNotRunnableError(f"{build.entry_point} exposes no run_case(case) -> CaseOutcome")
    return found


def new_cycle() -> UUID:
    """A fresh correlation id for one end-to-end run."""
    return uuid4()
