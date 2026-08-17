"""From a column that disagreed to the file somebody should open.

Three links, and every one is a fact that was written down rather than inferred:

    column  →  the primitive whose `fills` target it     the frozen spec
            →  `build.json`'s file_map                   whoever generated it
            →  a path under agents/<slug>/

Nothing here reads a `[from primitive X]` header. That convention is what the
`file_map` column exists to replace, because it fails silently the moment a file
is renamed — and a patch applied to the wrong file still passes the gate, for
the wrong reason.

**Where a link breaks, this says so.** A column nothing fills and a map that has
gone stale are both real states, and in both the honest answer is a bundle that
names the problem rather than a plausible path that wastes somebody's afternoon.

One caveat the repair skill is told about and this module cannot fix:
`primitive_key` names where a failure was *detected*, not where it was *caused*.
A check reporting nothing matched may be perfect while its input never arrived,
which is why the bundle carries the trace as well as the file.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from meridian.domain.frozen import FrozenSpec
from meridian.domain.primitives import CheckConfig
from meridian.healing.compare import Mismatch
from meridian.runtime.trace import Step

Detector = Literal["assertion", "output_diff", "conformance"]

ENTRY_POINT = "entry_point"


@dataclass(frozen=True)
class Located:
    """One failure, bucketed and addressed.

    `signature` is the bucketing key, and it is what the repair loop counts
    cases by and looks up history against — so it must be stable across builds
    and must not contain a value from any particular case.
    """

    signature: str
    detector: Detector
    primitive_key: str | None = None
    file: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def owners_from_spec(spec: FrozenSpec) -> dict[str, tuple[str, ...]]:
    """Which primitive fills each output column.

    A tuple rather than a key, because two checks filling one column is possible
    and picking one of them at random is how a patch lands in a file that had
    nothing to do with the failure.
    """
    owners: dict[str, list[str]] = {}
    for key, card in sorted(spec.primitives.items()):
        if not isinstance(card.config, CheckConfig):
            continue
        for fill in card.config.fills:
            owners.setdefault(fill.field.path, []).append(key)
    return {column: tuple(keys) for column, keys in owners.items()}


def locate(mismatch: Mismatch, spec: FrozenSpec, file_map: Mapping[str, str]) -> Located:
    """Address one disagreeing column."""
    owners = owners_from_spec(spec).get(mismatch.column, ())
    detail: dict[str, Any] = {
        "column": mismatch.column,
        "expected": mismatch.expected,
        "actual": mismatch.actual,
    }

    if len(owners) != 1:
        # Both ends of the same honesty. Nothing fills it, or too much does —
        # either way there is no single file to name, and the generator's own
        # stop condition ("an eval column nothing fills") is the finding.
        why = "unfilled" if not owners else "ambiguous"
        return Located(
            signature=f"{mismatch.column} :: output_diff :: {why}",
            detector="output_diff",
            detail=detail | ({"candidates": list(owners)} if owners else {}),
        )

    (owner,) = owners
    return Located(
        signature=f"{owner} :: output_diff :: {mismatch.column}",
        detector="output_diff",
        primitive_key=owner,
        file=_path(spec, file_map, owner),
        detail=detail | _stale(file_map, owner),
    )


def locate_error(
    errored: str,
    steps: Sequence[Step],
    spec: FrozenSpec,
    file_map: Mapping[str, str],
) -> Located:
    """Address a case that raised rather than disagreeing.

    One row, not one per column. Seven columns are all wrong for the same single
    reason, and burying that reason under seven identical buckets is how a
    diagnosis stops being readable.

    With no steps at all the locator is the entry point — which is the tell the
    repair skill works from: nothing got far enough to be named, so the fix is
    upstream of everything the eval measures and is found by working forwards
    rather than by opening the file a bundle points at.
    """
    failed = next((s for s in reversed(steps) if s.status == "failed"), None)
    locator = failed.name if failed else (steps[-1].name if steps else ENTRY_POINT)
    known = locator in spec.primitives

    return Located(
        signature=f"{locator} :: assertion :: {errored.split(':', 1)[0].strip()}",
        detector="assertion",
        # A step is not always a card. `extract` is a real step with no
        # primitive behind it, and writing it into `failures.primitive_key`
        # would put a non-existent key in a column everything else joins on.
        primitive_key=locator if known else None,
        file=_path(spec, file_map, locator) if known else None,
        detail={"error": errored, "reached": [s.name for s in steps]},
    )


def _path(spec: FrozenSpec, file_map: Mapping[str, str], key: str) -> str | None:
    relative = file_map.get(key)
    return f"agents/{spec.slug}/{relative}" if relative else None


def _stale(file_map: Mapping[str, str], key: str) -> dict[str, Any]:
    if key in file_map:
        return {}
    return {"note": f"no file_map entry for {key!r} — build.json is stale or incomplete"}
