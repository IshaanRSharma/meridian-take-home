"""The end state: nothing outstanding, and nothing to send.

``effect: noop`` with ``is_terminal: true`` and no capabilities, so there is no
activity here and nothing crosses a boundary. It exists as a card because a
named end state is what makes "the process finished cleanly" different from
"the process stopped somewhere", and the trace has to be able to tell a reader
which of those happened.

The context settles what reaching it means: *"A shipment resolves on its own
once the corrected line stops failing."* Nothing marks it resolved; passing both
Checks is what resolving is.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def shipment_validated(card: Mapping[str, Any]) -> dict[str, Any]:
    """Record that the shipment finished with nothing outstanding.

    Args:
        card: this primitive's ``config`` from the frozen spec.

    Returns:
        What the trace should say about the end state, which is the whole of
        this step's output — there is no payload and no recipient.
    """
    return {
        "effect": str(card["effect"]),
        "terminal": bool(card["is_terminal"]),
        "resolution": "no outstanding discrepancies",
    }
