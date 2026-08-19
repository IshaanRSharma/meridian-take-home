"""The spec's entity fields, as a schema a model will actually honour.

``entities[key].fields`` is already JSON Schema, which is the point of putting it
in the spec — nothing here invents a shape. What it adds is the two things a
strict structured-output call requires and a spec has no reason to carry:
every object must list all of its properties as ``required`` and must forbid
extras.

Fields a Check reads are ``["string", "null"]`` on purpose. Making them required
does not make them present — it makes the model return the key with ``null``
rather than omitting the line item, which is exactly what the Check that exists
to detect a missing code needs to see.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

INSTANCES = "instances"


def strict(node: Mapping[str, Any]) -> dict[str, Any]:
    """One schema node with strict-mode obligations filled in, recursively."""
    out = dict(node)
    if out.get("type") == "object" or "properties" in out:
        properties = {k: strict(v) for k, v in (out.get("properties") or {}).items()}
        out["type"] = "object"
        out["properties"] = properties
        out["required"] = list(properties)
        out["additionalProperties"] = False
    if "items" in out:
        out["items"] = strict(out["items"])
    return out


def entity_schema(fields: Mapping[str, Any]) -> dict[str, Any]:
    """One instance of an entity, as a strict object schema."""
    return strict({"type": "object", "properties": dict(fields)})


def instances_schema(fields: Mapping[str, Any]) -> dict[str, Any]:
    """A *list* of instances, because one attachment may hold several.

    A single-document file is a list of length one, which means "is this one PDF
    or twelve" never has to be answered anywhere.
    """
    return strict(
        {
            "type": "object",
            "properties": {INSTANCES: {"type": "array", "items": entity_schema(fields)}},
        }
    )
