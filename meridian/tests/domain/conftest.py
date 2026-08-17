"""The pre-alert cards as they look once review is finished.

Every field a process owner would eventually supply, so the completeness
contract can be held to a floor: a finished card reports nothing. Rules that
still fire on a finished board are too fussy, and a process owner shown six
findings on work they consider done stops reading findings.

Derived from the SOP in ``docs/sop-inbound-pre-alert.pdf``.
"""

import pytest

from meridian.domain.primitives import (
    ActionConfig,
    Cardinality,
    CheckConfig,
    Criterion,
    EntityConfig,
    EventConfig,
    FieldRef,
    Operand,
    Outcome,
    RoleRef,
    Timing,
)

INVOICE_NO = FieldRef(entity="commercial_invoice", path="invoice_no")
BATCH_NO = FieldRef(entity="commercial_invoice", path="line_items[].batch_no")
DRUG_DESCRIPTION = FieldRef(entity="commercial_invoice", path="line_items[].drug_description")


@pytest.fixture
def commercial_invoice() -> EntityConfig:
    """One per shipment, and the entity every other check hangs off."""
    return EntityConfig(
        name="Commercial Invoice",
        identified_by='the page header reads "Commercial Invoice"',
        cardinality=Cardinality(kind="one"),
        fields={
            "invoice_no": {"type": "string"},
            "container_no": {"type": ["string", "null"]},
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    # Fields a Check requires are nullable here on purpose: if
                    # extraction refused to return a line item missing an HTS
                    # number, the Check could never detect the failure it exists
                    # to detect.
                    "properties": {
                        "drug_description": {"type": "string"},
                        "batch_no": {"type": ["string", "null"]},
                        "hts_number": {"type": ["string", "null"]},
                        "fda_product_code": {"type": ["string", "null"]},
                        "ndc_number": {"type": ["string", "null"]},
                        "anda_number": {"type": ["string", "null"]},
                    },
                },
            },
        },
        sample_path="fixtures/documents/U03-4790.pdf",
        sample_extracted={"invoice_no": "U03/25-26/4790", "line_items": []},
    )


@pytest.fixture
def certificate_of_analysis() -> EntityConfig:
    """One per batch, so the invoice determines how many to expect."""
    return EntityConfig(
        name="Certificate of Analysis",
        identified_by='the page header reads "Certificate of Analysis"',
        cardinality=Cardinality(kind="one_per", per=BATCH_NO),
        fields={
            "batch_no": {"type": "string"},
            "product_code": {"type": ["string", "null"]},
        },
        sample_path="fixtures/documents/coa-sample.pdf",
        sample_extracted={"batch_no": "UAC25022"},
    )


@pytest.fixture
def prealert_received() -> EventConfig:
    """The pre-alert email lands, carrying an invoice and some COAs."""
    return EventConfig(
        name="Pre-alert documents received",
        channel="email",
        correlation_key=FieldRef(entity="commercial_invoice", path="container_no"),
        match_condition='subject contains "Pre-Alert Documents" or "PRE-ALERT DOCUMENTATION"',
        captures=["commercial_invoice", "certificate_of_analysis"],
        timing=Timing(kind="await", mode="on_arrival"),
        instructions="Documents arrive in pieces; a shipment may receive several emails.",
    )


@pytest.fixture
def invoice_complete() -> CheckConfig:
    """Every line item carries the four regulatory identifiers."""
    return CheckConfig(
        name="Does every line item have HTS, FDA product code, NDC and ANDA?",
        criteria=[
            Criterion(op="present", left=FieldRef(entity="commercial_invoice", path=path))
            for path in (
                "line_items[].hts_number",
                "line_items[].fda_product_code",
                "line_items[].ndc_number",
                "line_items[].anda_number",
            )
        ],
        scope="per_line_item",
        quantifier="all",
        inputs=["commercial_invoice"],
        outcomes=[
            Outcome(name="pass", priority=0, description="every line item is complete"),
            Outcome(name="missing_information", priority=1, description="a field is absent"),
        ],
        evidence=[INVOICE_NO, DRUG_DESCRIPTION],
        on_missing_input="wait",
    )


@pytest.fixture
def coas_valid() -> CheckConfig:
    """Every batch on the invoice has a matching Certificate of Analysis."""
    return CheckConfig(
        name="Does every batch on the invoice have a matching COA?",
        criteria=[
            Criterion(
                op="each_has_matching",
                left=BATCH_NO,
                right=Operand(
                    kind="field",
                    field=FieldRef(entity="certificate_of_analysis", path="batch_no"),
                ),
            )
        ],
        scope="per_case",
        quantifier="all",
        inputs=["commercial_invoice", "certificate_of_analysis"],
        outcomes=[
            Outcome(name="pass", priority=0),
            Outcome(name="missing_coa", priority=1, description="a batch carries no COA"),
            Outcome(name="mismatched_coa", priority=2, description="the batch number disagrees"),
        ],
        evidence=[INVOICE_NO, BATCH_NO],
        on_missing_input="wait",
    )


@pytest.fixture
def report_invoice_discrepancy() -> ActionConfig:
    """Email the supervisor with invoice number, drug description and what is missing."""
    return ActionConfig(
        name="Report the invoice discrepancy",
        effect="notify",
        channel="email",
        recipients=[RoleRef(role="receiving_supervisor")],
        payload_fields=[INVOICE_NO, DRUG_DESCRIPTION],
        inputs=["commercial_invoice"],
        idempotency_key="invoice_no",
        instructions="Name the missing information type explicitly, per the SOP.",
    )


@pytest.fixture
def report_coa_discrepancy() -> ActionConfig:
    """Email the supervisor with invoice number, batch numbers and the discrepancy."""
    return ActionConfig(
        name="Report the COA discrepancy",
        effect="notify",
        channel="email",
        recipients=[RoleRef(role="receiving_supervisor")],
        payload_fields=[INVOICE_NO, BATCH_NO],
        inputs=["certificate_of_analysis"],
        idempotency_key="invoice_no",
    )


@pytest.fixture
def documentation_validated() -> ActionConfig:
    """A named end state: terminal, and it does nothing but say so."""
    return ActionConfig(
        name="Documentation validated",
        effect="noop",
        is_terminal=True,
    )
