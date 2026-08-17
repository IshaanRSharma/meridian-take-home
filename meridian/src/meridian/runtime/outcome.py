"""What a Check returns.

Counts, not a boolean. The deliverable for the running example is a per-case row
of aggregates — ``CoA (Success) 5 / CoA (Total) 5`` — so a Check that answered
true or false would throw away the thing the process exists to produce.

``total``, ``passed`` and ``failed`` mirror the three measures a ``Fill`` can
project into an output entity: ``checked``, ``passed``, ``failed``.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RuntimeModel(BaseModel):
    """Frozen and closed, like the domain types this mirrors.

    Results are evidence. A later step adjusting a count would make the trace
    disagree with what actually happened.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class Failure(RuntimeModel):
    """One row that did not satisfy a Check, and enough context to see why.

    The failure bundle is pasted into a coding agent with no further lookup, so
    a failure naming only its position sends a human back to the source
    documents. ``detail`` carries the values, which is what turns a diagnosis
    into a single read: ``['UAC25022 ', 'uac25019']`` shows trailing whitespace
    and case variance without anybody explaining it.
    """

    grain: str
    locator: str
    reason: str
    detail: dict[str, Any] = Field(default_factory=dict)


class CheckResult(RuntimeModel):
    """The aggregate a Check reports for one case.

    ``outcome`` is a name the process owner declared on the card, not a boolean
    and not an enum this module owns — generated routing switches on it.
    """

    outcome: str
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    failures: tuple[Failure, ...] = ()

    @model_validator(mode="after")
    def _counts_reconcile(self) -> Self:
        """Every examined row either passed or failed.

        Enforced here rather than trusted per Check: the eval row is arithmetic,
        and a Check that reported 5 total with 3 passed and 1 failed would
        produce a column that is quietly wrong with nothing downstream to catch
        it. A row nobody examined is not counted in ``total`` at all.
        """
        if self.passed + self.failed != self.total:
            msg = (
                f"{self.passed} passed + {self.failed} failed != {self.total} total; "
                "a row that was examined must land on one side or the other"
            )
            raise ValueError(msg)
        return self

    def examined_anything(self) -> bool:
        """Whether there was anything to check.

        Zero line items is a real state — an invoice carrying no goods — and it
        is not the same as everything passing. One is a data problem.
        """
        return self.total > 0
