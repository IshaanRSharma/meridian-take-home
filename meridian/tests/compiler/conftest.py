"""A board with nothing wrong with it.

Shared by the rule tests and the decision tests, and the two use it for opposite
purposes. Lint must be silent on it — a rule that fires on a finished board is
noise. Decisions must NOT be silent on it, because a finished board still makes
choices, and that difference is the whole reason the two exist separately.
"""

import pytest

from meridian.domain.graph import (
    ActionPrimitive,
    Board,
    CheckPrimitive,
    Edge,
    EntityPrimitive,
    EventPrimitive,
)
from meridian.domain.primitives import (
    ActionConfig,
    CheckConfig,
    Criterion,
    EntityConfig,
    EventConfig,
    FieldRef,
    Outcome,
    RoleRef,
)

PASS = Outcome(name="pass")
FAIL = Outcome(name="fail")


@pytest.fixture
def sound() -> Board:
    """A small board with nothing wrong with it."""
    return Board(
        name="sound",
        primitives=[
            EntityPrimitive(
                key="invoice",
                config=EntityConfig(
                    name="Invoice", identified_by="header reads Invoice", fields={"no": {}}
                ),
            ),
            EventPrimitive(
                key="arrived",
                config=EventConfig(
                    name="Arrived",
                    correlation_key=FieldRef(entity="invoice", path="no"),
                    match_condition="subject contains Invoice",
                    captures=["invoice"],
                    timing={"kind": "await"},
                ),
            ),
            CheckPrimitive(
                key="looks_ok",
                config=CheckConfig(
                    name="Looks ok?",
                    criteria=[Criterion(op="present", left=FieldRef(entity="invoice", path="no"))],
                    inputs=["invoice"],
                    outcomes=[PASS, FAIL],
                    evidence=[FieldRef(entity="invoice", path="no")],
                    on_missing_input="wait",
                ),
            ),
            ActionPrimitive(
                key="done", config=ActionConfig(name="Done", effect="noop", is_terminal=True)
            ),
            ActionPrimitive(
                key="complain",
                config=ActionConfig(
                    name="Complain",
                    effect="notify",
                    channel="email",
                    recipients=[RoleRef(role="supervisor")],
                    payload_fields=[FieldRef(entity="invoice", path="no")],
                    idempotency_key="no",
                    is_terminal=True,
                ),
            ),
        ],
        edges=[
            Edge(key="e1", from_key="arrived", to_key="looks_ok"),
            Edge(key="e2", from_key="looks_ok", to_key="done", on_outcomes=["pass"]),
            Edge(key="e3", from_key="looks_ok", to_key="complain", on_outcomes=["fail"]),
        ],
    )
