"""Which step runs next.

The edge table is the transition relation, and this is the only place it is
interpreted. Two properties matter more than the rest: an unconditional edge
fires whatever the outcome, and an outcome nobody wired is a stop rather than a
crash — lint warns about that board, but a running agent still has to survive it.
"""

import pytest

from meridian.runtime.routing import AmbiguousRouteError, Routes

# The seed board's transition relation, verbatim.
SEED = [
    ("e1", "prealert_received", "invoice_complete", ()),
    ("e2", "invoice_complete", "coas_valid", ("pass",)),
    ("e3", "invoice_complete", "report_invoice_discrepancy", ("missing_information",)),
    ("e4", "coas_valid", "documentation_validated", ("pass",)),
    ("e5", "coas_valid", "report_coa_discrepancy", ("missing_coa",)),
]


@pytest.fixture
def routes() -> Routes:
    return Routes.from_edges(SEED)


def test_an_edge_with_no_outcomes_fires_whatever_happened(routes: Routes):
    # An Event has no outcomes to branch on, so its edge is unconditional.
    assert routes.next("prealert_received", "anything") == "invoice_complete"
    assert routes.next("prealert_received", "") == "invoice_complete"


def test_an_outcome_routes_to_the_step_it_was_wired_to(routes: Routes):
    assert routes.next("invoice_complete", "pass") == "coas_valid"
    assert routes.next("invoice_complete", "missing_information") == "report_invoice_discrepancy"


def test_an_unwired_outcome_stops_rather_than_crashing(routes: Routes):
    # `mismatched_coa` is declared on the card and no edge carries it. The
    # compiler reports that as blocking, but an agent built from an older spec
    # must not blow up mid-shipment — it stops, and the trace shows where.
    assert routes.next("coas_valid", "mismatched_coa") is None


def test_a_step_with_no_outgoing_edges_is_a_terminal(routes: Routes):
    assert routes.next("documentation_validated", "pass") is None
    assert routes.is_terminal("documentation_validated")
    assert not routes.is_terminal("coas_valid")


def test_two_edges_claiming_one_outcome_is_an_error(routes: Routes):
    # Arbitrary behaviour here would be worse than stopping: the same input
    # would take different paths depending on edge ordering, and a failing eval
    # case could not be reproduced.
    ambiguous = Routes.from_edges(
        [*SEED, ("e6", "invoice_complete", "documentation_validated", ("pass",))]
    )
    with pytest.raises(AmbiguousRouteError, match="pass"):
        ambiguous.next("invoice_complete", "pass")


def test_a_repeat_edge_is_an_ordinary_next_step(routes: Routes):
    # The board is a state machine, not a DAG. Routing backwards is normal, and
    # nothing here treats a cycle as special.
    cyclic = Routes.from_edges(
        [*SEED, ("e6", "report_coa_discrepancy", "coas_valid", ("corrected",))]
    )
    assert cyclic.next("report_coa_discrepancy", "corrected") == "coas_valid"


def test_an_unknown_step_has_nowhere_to_go(routes: Routes):
    assert routes.next("a_step_that_is_not_on_this_board", "pass") is None
