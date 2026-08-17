"""The four operators, one function each.

Each takes rows and reports which of them held. Nothing here counts, orders
outcomes or fills anything — that is ``counting`` and ``fills``. A criterion
answers one question about one row.

**No normalisation.** ``each_has_matching`` compares exactly, because the
criterion says ``each_has_matching`` and not *case-insensitively, after
trimming*. Deciding otherwise would be inventing a rule the process owner never
stated, and it is the first repair the eval suite is expected to demand.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from meridian.domain.primitives import Scope
from meridian.runtime.check.paths import Row
from meridian.runtime.outcome import Failure

_COMPARISONS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "in": lambda a, b: a in b,
}


def present(rows: Sequence[Row], grain: Scope) -> list[Failure]:
    """Every row must carry a value. Blank counts as absent."""
    return [
        Failure(
            grain=grain,
            locator=row.locator,
            reason="required, and this does not carry it",
            detail={"field": row.locator.rsplit(".", 1)[-1]},
        )
        for row in rows
        if row.value is None or not str(row.value).strip()
    ]


def compare(rows: Sequence[Row], operator: str, against: Any, grain: Scope) -> list[Failure]:
    """Every row must satisfy ``operator`` against a resolved right-hand side.

    ``against`` is already resolved — a literal, another field's value, or
    ``ctx.clock``. Resolving it here would mean reaching for a clock, which is
    the nondeterminism the workflow sandbox exists to prevent.
    """
    test = _COMPARISONS.get(operator)
    failures: list[Failure] = []
    for row in rows:
        if operator == "matches":
            ok = row.value is not None and re.search(str(against), str(row.value)) is not None
        elif test is None or row.value is None:
            ok = False
        else:
            ok = bool(test(row.value, against))
        if not ok:
            failures.append(
                Failure(
                    grain=grain,
                    locator=row.locator,
                    reason=f"is not {operator} {against!r}",
                    detail={"actual": row.value, "expected": against},
                )
            )
    return failures


def each_has_matching(
    rows: Sequence[Row], candidates: Iterable[Any], grain: Scope
) -> list[Failure]:
    """Every row's value must appear among the candidates.

    The failure carries both the value that did not match and every value that
    was available, because that pair is the diagnosis: ``'UAC25022 '`` against
    ``['UAC25022']`` shows trailing whitespace without anybody explaining it.
    """
    index = {value for value in candidates if value is not None}
    return [
        Failure(
            grain=grain,
            locator=row.locator,
            reason="nothing on the other side carries this value",
            detail={"value": row.value, "available": sorted(map(str, index))},
        )
        for row in rows
        if row.value not in index
    ]
