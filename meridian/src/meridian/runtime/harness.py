"""What a generated agent hands back for one eval case.

The sweep has to run *any* agent, and every interesting part of running one is
specific to the process it came from: which signals it takes, how many documents
it waits for, what its input looks like, which providers it wires up. A harness
that knew those things would know one agent.

So the split is the other way round. **The agent runs itself and returns this;
the harness loads it, compares it and stores it.** ``build.json`` names an entry
point module exposing one coroutine::

    async def run_case(case: Mapping[str, Any]) -> CaseOutcome

``case`` is ``eval_cases.input`` verbatim. Everything Temporal — starting an
environment, registering a worker, sending signals — happens inside that
function, where the agent's own vocabulary is in scope.

``CaseOutcome`` is built *outside* the workflow, so it never crosses Temporal's
payload converter. How the trace leaves the sandbox is the agent's business;
``RunTrace.dump()`` and ``from_dump`` make the obvious way a single line at each
end, because a JSON string crosses the boundary where a model would not.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from meridian.runtime.trace import Declined, Step


class CaseOutcome(BaseModel):
    """One eval case, as it actually ran.

    ``output`` is the row the process produces, compared column by column
    against ``eval_cases.expected_output``. It is deliberately untyped: the
    columns belong to the customer's process, not to this module, and a schema
    here would have to be edited every time a board grows a field.

    Everything else is the trace. It is optional because a build that runs the
    cases and records nothing still scores — that is the first point on the
    curve, and refusing it would lose it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    output: dict[str, Any] = Field(default_factory=dict)
    steps: tuple[Step, ...] = ()
    declined: tuple[Declined, ...] = ()
    spec_version: int = 0
    spec_checksum: str = ""

    @classmethod
    def from_dump(cls, output: Mapping[str, Any], dump: str) -> CaseOutcome:
        """Rebuild from an eval row and a ``RunTrace.dump()``.

        Validation is the point. Rule 4 asks a generated agent to keep the
        trace's shape, and a malformed step caught here names the field that is
        wrong — where the same step reaching ``run_steps`` would be a row
        nothing could localise from, discovered much later by a person reading a
        bundle with a hole in it.
        """
        return cls.model_validate(dict(json.loads(dump)) | {"output": dict(output)})
