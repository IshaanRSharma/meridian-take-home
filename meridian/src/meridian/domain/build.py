"""What happened after the freeze: builds, cases, and repairs.

Before the freeze the types describe a process somebody is still drawing. These
describe attempts at implementing it — and the difference that matters is that
every one of them is *evidence*, so all three are immutable like the rest of
`domain/`. A build is a commit, a case is a question with an answer already
known, a repair is a diff somebody either accepted or did not.

`run_steps` deliberately has no type here. It is a trace that `runtime` already
defines and `CaseOutcome` already validated on arrival, so a second model would
re-check the same fields under a second name — the duplication being worse than
the dicts it would replace.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from meridian.domain.primitives import DomainModel

CreatedBy = Literal["codegen", "repair", "human"]
Split = Literal["train", "holdout"]
Origin = Literal["authored", "scenario", "inbox", "mutation"]
Classification = Literal["implementation_defect", "spec_gap"]
RepairStatus = Literal["proposed", "regressed", "accepted", "rejected", "escalated"]


class Build(DomainModel):
    """One attempt at implementing a spec, as a commit.

    `model`, `prompt_version` and `temperature` are here because reproducibility
    is a claim about builds and the same spec generated twice by different
    models is two different programs. Recording them is what makes "regenerate
    it" a instruction rather than a hope.

    `file_map` is the load-bearing one. Localisation resolves
    `primitive_key → file` from it, and the alternative — a `[from primitive X]`
    header convention — fails silently the first time anything is renamed.
    """

    id: UUID | None = None
    spec_id: UUID
    iteration: int = 1
    parent_build_id: UUID | None = None
    source_ref: str
    created_by: CreatedBy = "codegen"
    model: str | None = None
    prompt_version: str | None = None
    temperature: float | None = None
    file_map: dict[str, str] = Field(default_factory=dict)
    entry_point: str | None = None
    created_at: datetime | None = None

    @property
    def identity(self) -> UUID:
        """This build's id, insisting that it has one.

        `id` is optional because an unsaved build is a real thing — it is what
        `register` hands the repository. Everything downstream of that receives
        a build that came *back* from the database, and would otherwise carry an
        optional through every signature to model a state none of them can be in.
        """
        if self.id is None:
            raise ValueError("this build has not been registered, so it has no id")
        return self.id

    def slug(self) -> str:
        """The agent's own directory name, without whatever holds it.

        Read off `source_ref`, which is `<root>/<slug>@<sha>` — one string
        rather than a column, because the two halves are never useful apart:
        every use is either "where is the code" or "which version of it".

        The **last** segment, not the part after `agents/`. The root is a
        parameter — a candidate build is measured somewhere other than the shelf
        — so stripping a fixed prefix would leave the root in the slug and every
        path built from it would name `<root>/<root>/<slug>`.
        """
        return self.source_ref.split("@")[0].rstrip("/").rsplit("/", 1)[-1]

    def commit(self) -> str:
        """The sha this build's code is at."""
        _, _, sha = self.source_ref.partition("@")
        return sha


class EvalCase(DomainModel):
    """One question the suite already knows the answer to.

    `input` is handed to the agent's entry point verbatim; `expected_output` is
    the row it should produce. Held apart from the board on purpose: the eval
    set's *shape* informs the reviewer's questions, its *values* are this loop's
    oracle, and letting the values into elicitation fits the spec to the test.
    """

    id: UUID | None = None
    spec_id: UUID | None = None
    key: str
    split: Split = "train"
    origin: Origin = "authored"
    scenario_key: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    expected_output: dict[str, Any] = Field(default_factory=dict)
    tags: tuple[str, ...] = ()


class RunResult(DomainModel):
    """One case as it ran, beside the answer it should have given.

    Read back rather than recomputed, and that is what makes the gate cheap:
    "nothing that passed before now fails" is a comparison of two builds' stored
    rows, so it costs a query rather than a second sweep of the parent build.

    `expected_output` travels with it because the comparison is per column, and
    a run without the row it was measured against is a set of numbers with
    nothing to say.
    """

    run_id: UUID
    case_id: UUID | None
    case_key: str
    outcome: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    expected_output: dict[str, Any] = Field(default_factory=dict)
    errored: str | None = None


class Repair(DomainModel):
    """One patch against one signature, and what the gate made of it.

    `failing_case_ids` and `regressed_case_ids` are computed from two sweeps
    rather than reported by whoever wrote the patch. That is the whole point of
    the gate: it can only reject, a human is required to override and never to
    approve, and neither is possible if the numbers it judges on were typed in
    by the party being judged.

    A `spec_gap` without `raised_thread_id` is refused by the table itself. A
    decision nobody can settle in code must go back to the process owner, and
    leaving that to discipline is how it quietly stops happening.
    """

    id: UUID | None = None
    build_id: UUID
    classification: Classification
    failure_signature: str
    failing_case_ids: tuple[UUID, ...] = ()
    files_touched: tuple[str, ...] = ()
    summary: str
    diff: str | None = None
    status: RepairStatus = "proposed"
    regressed_case_ids: tuple[UUID, ...] = ()
    produced_build_id: UUID | None = None
    raised_thread_id: UUID | None = None
    created_at: datetime | None = None
