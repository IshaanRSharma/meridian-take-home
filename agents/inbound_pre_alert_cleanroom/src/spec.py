"""The frozen spec, read rather than copied.

`fields`, `criteria`, `fills` and `payload_fields` are already exactly what the
code needs, so restating any of them in Python would create two sources of truth
that drift the first time the board is revised. Everything here is a lookup into
`spec.lock.json`, which is checksummed and never edited.

The output entity is *derived* rather than named. Nothing in the spec labels it,
but it is definable: the entity no Event captures, no Action produces, and only
`fills` write into. Deriving it means a board that grows a second such entity
fails loudly here rather than silently filling the wrong row.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

SPEC_PATH = Path(__file__).resolve().parent.parent / "spec.lock.json"
SPEC: dict[str, Any] = json.loads(SPEC_PATH.read_text())

VERSION: int = int(SPEC["version"])
CHECKSUM: str = str(SPEC["checksum"])

PRIMITIVES: Mapping[str, Any] = SPEC["primitives"]
ENTITIES: Mapping[str, Any] = SPEC["entities"]


class SpecError(RuntimeError):
    """The spec does not have the shape this agent was generated for.

    Raised rather than defaulted. A board revision that removes an entity or
    renames an outcome should stop the agent at import, where the message names
    the spec, instead of producing a row that is quietly wrong.
    """


def card(key: str) -> Mapping[str, Any]:
    """One primitive, by the key the file map and the failure bundle use."""
    try:
        return cast("Mapping[str, Any]", PRIMITIVES[key])
    except KeyError:
        raise SpecError(f"spec {CHECKSUM[:12]} has no primitive {key!r}") from None


def config(key: str) -> Mapping[str, Any]:
    """One primitive's config — criteria, fills, payload_fields and the rest."""
    return cast("Mapping[str, Any]", card(key)["config"])


def entity(key: str) -> Mapping[str, Any]:
    """One entity, including the JSON Schema an extractor is handed verbatim."""
    try:
        return cast("Mapping[str, Any]", ENTITIES[key])
    except KeyError:
        raise SpecError(f"spec {CHECKSUM[:12]} has no entity {key!r}") from None


def keys_of(primitive_type: str) -> tuple[str, ...]:
    """Every primitive of one kind, in spec order."""
    return tuple(k for k, v in PRIMITIVES.items() if v["primitive_type"] == primitive_type)


def edge_rows() -> tuple[tuple[str, str, str, tuple[str, ...]], ...]:
    """The transition relation as ``runtime.routing.Routes.from_edges`` wants it."""
    return tuple(
        (str(e["key"]), str(e["from_key"]), str(e["to_key"]), tuple(e.get("on_outcomes") or ()))
        for e in SPEC["edges"]
    )


def capability_of(key: str) -> str | None:
    """The one capability a card reaches the world through, if it reaches at all.

    A card with no capabilities is workflow code, which is what keeps a failing
    eval case a logic failure rather than a slow network.
    """
    declared = card(key).get("capabilities") or []
    if len(declared) > 1:
        raise SpecError(f"{key} declares {len(declared)} capabilities; this agent dispatches one")
    return str(declared[0]) if declared else None


def _fill_entities() -> set[str]:
    return {
        str(fill["field"]["entity"])
        for key in keys_of("check")
        for fill in config(key).get("fills") or []
    }


def _captured_entities() -> set[str]:
    captured: set[str] = set()
    for key in keys_of("event"):
        captured |= {str(name) for name in config(key).get("captures") or []}
    for key in keys_of("action"):
        produces = config(key).get("produces")
        if produces:
            captured.add(str(produces))
    return captured


def _output_entity() -> str:
    """The row the process produces, found rather than named.

    Two candidates or none means the workflow has no defensible return value,
    which is a stop condition rather than something to guess at.
    """
    candidates = sorted(_fill_entities() - _captured_entities())
    if len(candidates) != 1:
        raise SpecError(
            f"expected exactly one entity that is only ever filled; found {candidates or 'none'}"
        )
    return candidates[0]


OUTPUT_ENTITY: str = _output_entity()
OUTPUT_FIELDS: tuple[str, ...] = tuple(entity(OUTPUT_ENTITY)["fields"])


def envelope_entity(delivered_fields: Sequence[str]) -> str | None:
    """The captured entity that *is* the delivery, not something delivered inside it.

    An Event with ``channel: email`` captures the email alongside its
    attachments, and the email is not a page any recognition rule could match —
    its ``identified_by`` describes how it arrives. Rather than naming it in
    code, it is the capture whose fields the transport itself can already fill,
    which stays true if the board renames it.
    """
    for key in keys_of("event"):
        for name in config(key).get("captures") or []:
            fields = set(entity(str(name))["fields"])
            if fields and fields <= set(delivered_fields):
                return str(name)
    return None
