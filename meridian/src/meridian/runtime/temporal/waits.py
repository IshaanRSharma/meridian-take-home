"""The two kinds of waiting a process does.

Waiting for **data** — documents arrive in pieces and a deadline says how long to
hold. Waiting for a **person** — an ``effect: decide`` step blocks on a signal,
with an SLA and a branch for nobody answering. They look alike and are not: one
resumes because information arrived, the other because a human acted or failed
to.

Both wrap ``workflow.wait_condition``, which raises on timeout. These return a
bool instead, because a deadline passing is an ordinary outcome the process
owner drew a path for — not an error.

**Two hazards from the SDK, both worth knowing before a spec revision:**

* ``timeout=None`` creates no Temporal timer at all, so adding or removing a
  deadline on a card is a *nondeterministic change* for workflows already in
  flight. A spec revision that introduces a deadline cannot be applied to
  running instances.
* ``timeout=0`` throws immediately. This is why an absent deadline parses to
  ``None`` rather than ``timedelta(0)`` — collapsing the two would turn "no time
  limit" into "expire at once".
"""

from __future__ import annotations

from collections.abc import Callable

from temporalio import workflow

from meridian.runtime.duration import parse


async def await_inputs(ready: Callable[[], bool], deadline: str | None = None) -> bool:
    """Wait until the inputs are complete, or the deadline passes.

    Returns ``True`` if ``ready()`` became true, ``False`` if the deadline
    expired first. With no deadline this waits indefinitely, bounded only by the
    workflow's own execution timeout.
    """
    try:
        await workflow.wait_condition(ready, timeout=parse(deadline))
    except TimeoutError:
        return False
    return True


async def await_decision(decided: Callable[[], bool], sla: str | None = None) -> bool:
    """Wait for a person to answer, or for the SLA to expire.

    Returns ``False`` when nobody answered in time, which is the branch
    ``on_timeout`` names. Identical in mechanics to :func:`await_inputs` and kept
    separate because the two mean different things at the call site and read
    differently in a trace — a document that never arrived is a supplier problem,
    an approval that never came is a staffing one.
    """
    return await await_inputs(decided, sla)
