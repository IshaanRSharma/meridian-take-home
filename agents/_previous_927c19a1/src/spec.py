"""The frozen spec, read at run time rather than copied into code.

Entity schemas, criteria and edges all live in `spec.lock.json`. Transcribing
them into Python would create a second copy that drifts the first time somebody
edits one and not the other, and the conformance check exists precisely to
verify the file this agent runs against is the file that was approved.

Loaded once at import. The file is immutable by construction, so a cache cannot
go stale.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

SPEC_PATH = Path(__file__).resolve().parent.parent / "spec.lock.json"

OUTPUT_ENTITY = "shipment_summary"
"""The row this process produces.

The one entity no Event captures and no Action produces — only `fills` write
into it. Named here once so nothing downstream has to rediscover it.
"""


@cache
def spec() -> dict[str, Any]:
    """The whole frozen spec."""
    loaded: dict[str, Any] = json.loads(SPEC_PATH.read_text())
    return loaded


def card(key: str) -> dict[str, Any]:
    """One primitive's entry, config and settled context together."""
    found: dict[str, Any] = spec()["primitives"][key]
    return found


def config(key: str) -> dict[str, Any]:
    """One primitive's config."""
    found: dict[str, Any] = card(key)["config"]
    return found


def entity(key: str) -> dict[str, Any]:
    """One entity's declaration: how to recognise it and what to read off it."""
    found: dict[str, Any] = spec()["entities"][key]
    return found


def entities() -> dict[str, dict[str, Any]]:
    """Every entity, keyed as the spec keys them."""
    found: dict[str, dict[str, Any]] = spec()["entities"]
    return found


def arriving_entities() -> tuple[str, ...]:
    """Entities that come in on an event, so ingestion has to recognise them.

    Read off `captures` rather than listed here: an entity the process produces
    is never something to look for among the attachments, and the distinction is
    already made on the event card.
    """
    captured: list[str] = []
    for entry in spec()["primitives"].values():
        if entry["primitive_type"] == "event":
            captured.extend(entry["config"].get("captures", ()))
    return tuple(dict.fromkeys(captured))


def edges() -> tuple[tuple[str, str, str, tuple[str, ...]], ...]:
    """Every transition, in the shape `Routes.from_edges` takes."""
    return tuple(
        (edge["key"], edge["from_key"], edge["to_key"], tuple(edge.get("on_outcomes", ())))
        for edge in spec()["edges"]
    )


def version() -> int:
    """Which frozen version this agent implements."""
    found: int = spec()["version"]
    return found


def checksum() -> str:
    """The checksum conformance verifies against."""
    found: str = spec()["checksum"]
    return found
