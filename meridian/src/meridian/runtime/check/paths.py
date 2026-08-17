"""Turning a field reference into the rows a Check examines.

A ``FieldRef`` names a path into an entity — ``line_items[].batch_no``. Resolving
it produces the *rows* a criterion is evaluated against, one per value, each
carrying enough to be named in a failure bundle and to be rolled up to a coarser
grain afterwards.

Two distinctions carry most of the weight here, and both are about absence:

* **A field that is missing is a row whose value is ``None``** — not a row that
  disappears. Fields a Check requires are nullable in the extraction schema on
  purpose, so that a line item arriving without a batch number is something the
  Check can see and fail on. A vanishing row would make ``present`` unable to
  report anything.
* **A list that is absent or empty produces no rows.** An invoice with no line
  items has nothing to check, which is a different situation entirely.

Getting those two the same way round is what makes ``0 checked`` mean "nothing
arrived" rather than "everything was fine".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_ITERATE = "[]"


class PathError(Exception):
    """A field reference and the data it was resolved against disagree.

    Raised rather than returning nothing, because silently reporting zero rows
    would let a Check pass for a reason nobody intended — and a spec whose paths
    do not match its schemas is a build problem, not a business outcome.
    """


@dataclass(frozen=True)
class Row:
    """One value a criterion is evaluated against."""

    value: Any
    locator: str
    document: int
    """Index of the document this came from, so counts can roll up by document."""
    indices: tuple[int, ...] = ()
    """Array positions crossed on the way here.

    Two criteria reading ``line_items[].hts_number`` and
    ``line_items[].batch_no`` produce different values at the same *place*, and
    a Check asks whether every criterion held on one line item. ``(document,
    indices)`` is that place — without it the engine could only count
    assertions, and "two codes missing" would be indistinguishable from "two
    line items incomplete".
    """


def resolve(entity: str, instances: Sequence[Mapping[str, Any]], path: str) -> tuple[Row, ...]:
    """Every row ``path`` addresses across the instances of one entity.

    ``instances`` is every document of that entity for this case — five
    certificates are five instances, and one invoice with five line items is one
    instance yielding five rows. The same criterion has to read both shapes.
    """
    # Only name the document when there is more than one; a bundle reading
    # `commercial_invoice.line_items[2]` is clearer than `[0].line_items[2]`,
    # and the index earns its place only when it disambiguates.
    numbered = len(instances) > 1
    rows: list[Row] = []
    for index, instance in enumerate(instances):
        root = f"{entity}[{index}]" if numbered else entity
        rows.extend(_walk(instance, path.split("."), root, index, ()))
    return tuple(rows)


def _walk(
    value: Any, segments: Sequence[str], locator: str, document: int, at: tuple[int, ...]
) -> list[Row]:
    if not segments:
        return [Row(value=value, locator=locator, document=document, indices=at)]

    head, rest = segments[0], segments[1:]
    iterated = head.endswith(_ITERATE)
    name = head[: -len(_ITERATE)] if iterated else head

    if value is None:
        # The parent was absent, so nothing below it exists to examine. Only
        # reached for intermediate segments; a missing leaf is handled below.
        return []
    if not isinstance(value, Mapping):
        msg = f"cannot read {name!r} from {type(value).__name__} at {locator}"
        raise PathError(msg)

    child = value.get(name)
    here = f"{locator}.{name}"

    if not iterated:
        if name not in value and not rest:
            # A leaf the document never carried. The row still exists, valueless,
            # so the Check can fail on it.
            return [Row(value=None, locator=here, document=document, indices=at)]
        return _walk(child, rest, here, document, at)

    if child is None:
        return []
    if not isinstance(child, Sequence) or isinstance(child, str | bytes):
        msg = f"{name!r} at {here} is {type(child).__name__}, not a list, but the path iterates it"
        raise PathError(msg)

    rows: list[Row] = []
    for position, item in enumerate(child):
        rows.extend(_walk(item, rest, f"{here}[{position}]", document, (*at, position)))
    return rows
