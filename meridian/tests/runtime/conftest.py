"""A context shaped like the one a generated agent receives, with no world attached."""

import pytest

from meridian.runtime.context import AgentContext, UnboundBindings, UnboundTools
from meridian.runtime.trace import RunTrace


@pytest.fixture
def ctx() -> AgentContext:
    """The contract, with every outward edge stubbed.

    Tools raise rather than return empty: a check that reaches for the world is
    a design error, and silence would let it pass.
    """
    return AgentContext(
        tools=UnboundTools(),
        bindings=UnboundBindings(),
        clock="2026-08-17T09:00:00Z",
        trace=RunTrace(spec_version=1, spec_checksum="test"),
    )
