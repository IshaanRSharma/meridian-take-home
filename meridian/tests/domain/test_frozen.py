"""Tests for the frozen spec.

Two properties carry this file. The checksum must depend on content and nothing
else, so that "the board changed after you submitted" is detectable rather than
asserted. And a primitive's capability list must decide whether it compiles to
an activity, because that is the determinism rule expressed as data.
"""

from datetime import UTC, datetime

from meridian.domain.frozen import FrozenSpec, ScopedContext, SpecPrimitive
from meridian.domain.graph import Edge
from meridian.domain.primitives import ActionConfig, CheckConfig, EntityConfig, RoleRef

REPORT = SpecPrimitive(
    key="report_coa_discrepancy",
    primitive_type="action",
    config=ActionConfig(
        name="Report the COA discrepancy",
        effect="notify",
        channel="email",
        recipients=[RoleRef(role="receiving_supervisor")],
        idempotency_key="invoice_no",
    ),
    capabilities=("email.send",),
)

CHECK = SpecPrimitive(
    key="coas_valid",
    primitive_type="check",
    config=CheckConfig(name="Does every batch have a COA?"),
    context=ScopedContext(
        inherited=("[rule] one container is one shipment",),
        local=("[exception] re-run every batch when a corrected COA arrives",),
        negative=("a COA whose batch is on no invoice line belongs to another shipment",),
        provenance=("th_02", "th_04"),
    ),
)


def spec() -> FrozenSpec:
    return FrozenSpec(
        entities={"commercial_invoice": EntityConfig(name="Commercial Invoice")},
        primitives={"coas_valid": CHECK, "report_coa_discrepancy": REPORT},
        edges=(Edge(key="e5", from_key="coas_valid", to_key="report_coa_discrepancy"),),
        capabilities=("email.send",),
    ).sealed()


# --- the checksum ----------------------------------------------------------


def test_a_sealed_spec_verifies():
    assert spec().is_intact()


def test_an_unsealed_spec_does_not_verify():
    assert not FrozenSpec().is_intact()


def test_two_freezes_of_the_same_content_agree():
    assert spec().checksum == spec().checksum


def test_changing_any_content_breaks_the_checksum():
    tampered = spec().model_copy(update={"capabilities": ("email.send", "sms.send")})
    assert not tampered.is_intact()


def test_editing_a_primitive_deep_in_the_spec_breaks_the_checksum():
    original = spec()
    renamed = REPORT.model_copy(
        update={"config": REPORT.config.model_copy(update={"channel": "sms"})}
    )
    tampered = original.model_copy(
        update={"primitives": original.primitives | {"report_coa_discrepancy": renamed}}
    )
    assert not tampered.is_intact()


def test_the_timestamp_is_outside_the_checksum():
    # Two freezes of an unchanged board must agree, and they cannot if the
    # moment of sealing is part of what is hashed.
    stamped = spec().model_copy(update={"frozen_at": datetime.now(UTC)})
    assert stamped.is_intact()


# --- what compiles to what -------------------------------------------------


def test_a_primitive_with_capabilities_compiles_to_an_activity():
    # Anything touching the world is an activity; anything deciding is workflow
    # code. A non-empty capability list is exactly that boundary.
    assert REPORT.is_activity()


def test_a_check_has_no_capabilities_and_stays_inline():
    assert not CHECK.is_activity()
    assert CHECK.capabilities == ()


def test_activities_come_back_in_key_order():
    assert [p.key for p in spec().activities()] == ["report_coa_discrepancy"]


# --- scoped context --------------------------------------------------------


def test_context_separates_what_was_inherited_from_what_was_local():
    assert CHECK.context.inherited == ("[rule] one container is one shipment",)
    assert "re-run every batch" in CHECK.context.local[0]


def test_provenance_points_back_at_the_conversations():
    assert CHECK.context.provenance == ("th_02", "th_04")


def test_a_primitive_review_settled_nothing_about_has_empty_context():
    assert REPORT.context.is_empty()
    assert not CHECK.context.is_empty()
