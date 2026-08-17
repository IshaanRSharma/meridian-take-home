"""Where a Check's counts land in the row the process produces.

A Check already reports ``total``, ``passed`` and ``failed``. A ``Fill`` says
which field of the output entity each of those goes into, and at what grain —
which is what lets one rule report at two granularities and produce two columns
of the eval row.

Nothing here decides what a column *means*. That decision is on the card, made
by the process owner, and reading it out is the whole job.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from meridian.domain.primitives import Scope
from meridian.runtime.check.counting import Tally
from meridian.runtime.outcome import Failure

_COUNTS = {"checked": "total", "passed": "passed", "failed": "failed"}


def apply_fills(
    row: MutableMapping[str, Any],
    fills: Sequence[Mapping[str, Any]],
    tallies: Mapping[str, Tally],
    scope: Scope,
    failures: Sequence[Failure] = (),
) -> MutableMapping[str, Any]:
    """Write each fill's count into the output row.

    ``tallies`` is keyed by grain — the Check's own scope, and any coarser grain
    it rolled up to. A fill naming a grain that was not computed is a build
    error rather than a zero: silently writing nothing would put a wrong number
    in a column and nothing downstream would notice.

    ``per`` defaults to the Check's own scope, so the ordinary case says nothing.
    """
    for fill in fills:
        if fill["measure"] == "failing":
            # Not a count. The distinct things that failed, in the order they
            # were found, so an email can name them rather than tally them.
            seen: dict[str, None] = {}
            for failure in failures:
                if failure.subject:
                    seen.setdefault(failure.subject, None)
            row[fill["field"]["path"]] = list(seen)
            continue

        grain = fill.get("per") or scope
        tally = tallies.get(grain)
        if tally is None:
            msg = f"fill wants a count {grain!r} but this check only produced {sorted(tallies)}"
            raise KeyError(msg)
        row[fill["field"]["path"]] = getattr(tally, _COUNTS[fill["measure"]])
    return row
