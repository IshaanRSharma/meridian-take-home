"""ISO 8601 durations, which is the only way the spec talks about time.

``PT48H`` is what a process owner's deadline compiles to, and it parses in
Python, TypeScript and Temporal alike — which is why the spec uses it. Temporal's
Python SDK takes ``timedelta``, so exactly one place converts.

Only the time-based designators are supported. Years and months are deliberately
absent: neither has a fixed length, so ``timedelta`` cannot represent them
honestly, and a process deadline measured in months would be a different feature
rather than a longer duration.
"""

from __future__ import annotations

import re
from datetime import timedelta

# Mirrors ISO_8601_DURATION in domain/primitives.py, minus years and months.
_PATTERN = re.compile(
    r"^P(?!$)(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?"
    r"(?:T(?=\d)(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


class DurationError(ValueError):
    """A duration the spec allows but this converter cannot represent."""


def parse(duration: str | None) -> timedelta | None:
    """``"PT48H"`` to a timedelta. ``None`` stays ``None``.

    A deadline that is absent is not a deadline of zero — it means the process
    owner did not put a time limit on this step, and the caller waits
    indefinitely. Collapsing the two would turn "no rush" into "expire at once".
    """
    if duration is None:
        return None

    match = _PATTERN.match(duration)
    if not match:
        msg = f"{duration!r} is not a duration this runtime can represent"
        raise DurationError(msg)

    parts = {unit: int(value) for unit, value in match.groupdict(default="0").items()}
    return timedelta(**parts)
