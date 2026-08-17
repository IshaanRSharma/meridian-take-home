"""Fixtures for the reviewer tests.

The seed board has exactly one way in, which is the case that needs no starting
point. A second way in is the case that does, and both modules behave
differently on it, so it lives here rather than in either test file.
"""

import pytest

from meridian.domain.graph import Board, Edge, EventPrimitive
from meridian.domain.primitives import EventConfig, Timing


@pytest.fixture
def two_entries(seed: Board) -> Board:
    """The seed board plus a second way information arrives.

    A corrected certificate turning up days later is the process owner's own
    example, and it is the answer that gives the board two entry points.
    """
    correction = EventPrimitive(
        key="correction_received",
        config=EventConfig(
            name="Corrected documents received",
            channel="email",
            timing=Timing(kind="await"),
        ),
    )
    return seed.model_copy(
        update={
            "primitives": (*seed.primitives, correction),
            "edges": (
                *seed.edges,
                Edge(key="e6", from_key="correction_received", to_key="coas_valid"),
            ),
        }
    )
