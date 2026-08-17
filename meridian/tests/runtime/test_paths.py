"""Turning a field reference into the rows a Check examines.

The fiddliest file in the skeleton, because one criterion has to be countable at
two grains: ``invoice_complete`` reports ``goods_failed: 2`` (line items) and
``invoices_failed: 1`` (the document containing them) from a single rule.

Most of these tests are about absence, which is where the subtlety is. A field
that is missing must still produce a row, or the Check that exists to detect
missing fields can never fail.
"""

import pytest

from meridian.runtime.check.paths import PathError, resolve

INVOICE = {
    "invoice_no": "CI-88213",
    "line_items": [
        {"batch_no": "UAC25019", "hts_number": "3004.90"},
        {"batch_no": "UAC25022", "hts_number": None},
        {"batch_no": None, "hts_number": "3004.90"},
    ],
}
COAS = [{"batch_no": "UAC25019"}, {"batch_no": "UAC25022"}]


def test_a_plain_field_yields_one_row_per_document():
    (row,) = resolve("commercial_invoice", [INVOICE], "invoice_no")
    assert row.value == "CI-88213"


def test_an_iterated_path_yields_one_row_per_item():
    rows = resolve("commercial_invoice", [INVOICE], "line_items[].batch_no")
    assert [r.value for r in rows] == ["UAC25019", "UAC25022", None]


def test_many_documents_of_one_entity_yield_a_row_each():
    # Five certificates are five documents, not one document with five rows.
    # The same criterion has to read both shapes.
    rows = resolve("certificate_of_analysis", COAS, "batch_no")
    assert [r.value for r in rows] == ["UAC25019", "UAC25022"]


def test_a_missing_field_is_a_row_with_no_value_not_a_missing_row():
    # THE property. Fields a Check requires are nullable in the extraction
    # schema on purpose, so that a line item arriving without a batch number is
    # something the Check can see and fail on. If the row vanished here,
    # `present` could never report anything.
    rows = resolve("commercial_invoice", [INVOICE], "line_items[].batch_no")
    assert len(rows) == 3
    assert rows[2].value is None


def test_an_absent_list_yields_no_rows_at_all():
    # Different from the case above, and the difference is real: an invoice with
    # no line items has nothing to check, which is not the same as a line item
    # that is missing a field.
    assert resolve("commercial_invoice", [{"invoice_no": "CI-1"}], "line_items[].batch_no") == ()


def test_an_empty_list_yields_no_rows():
    assert resolve("commercial_invoice", [{"line_items": []}], "line_items[].batch_no") == ()


def test_no_documents_yields_no_rows():
    # The certificate that never arrived. The Check reports 0 checked, and the
    # process owner's cardinality rule is what decides whether that is a failure.
    assert resolve("certificate_of_analysis", [], "batch_no") == ()


def test_a_locator_names_the_row_a_human_has_to_find():
    # This string is printed in the failure bundle. "row 3 failed" sends someone
    # back to the documents; this does not.
    rows = resolve("commercial_invoice", [INVOICE], "line_items[].batch_no")
    assert rows[1].locator == "commercial_invoice.line_items[1].batch_no"


def test_a_locator_distinguishes_documents_when_there_are_several():
    rows = resolve("certificate_of_analysis", COAS, "batch_no")
    assert rows[1].locator == "certificate_of_analysis[1].batch_no"


def test_iterating_something_that_is_not_a_list_is_an_error():
    # The extraction schema and the field reference disagree. Silently reporting
    # zero rows would make a Check pass for a reason nobody intended.
    with pytest.raises(PathError, match="line_items"):
        resolve("commercial_invoice", [{"line_items": "UAC25019"}], "line_items[].batch_no")


def test_reading_a_field_off_a_scalar_is_an_error():
    with pytest.raises(PathError):
        resolve("commercial_invoice", [{"invoice_no": "CI-1"}], "invoice_no.batch_no")


def test_the_whole_item_can_be_addressed():
    rows = resolve("commercial_invoice", [INVOICE], "line_items[]")
    assert rows[0].value == {"batch_no": "UAC25019", "hts_number": "3004.90"}
    assert rows[0].locator == "commercial_invoice.line_items[0]"


def test_rows_carry_the_document_they_came_from():
    # Rolling a line-item count up to a per-document count needs to know which
    # document each row belongs to. Without this, one criterion cannot report at
    # two grains and seven of the nine eval columns are unreachable.
    rows = resolve("certificate_of_analysis", COAS, "batch_no")
    assert [r.document for r in rows] == [0, 1]

    line_items = resolve("commercial_invoice", [INVOICE], "line_items[].batch_no")
    assert [r.document for r in line_items] == [0, 0, 0]


def test_a_key_the_document_never_carried_is_also_a_row():
    # Two shapes mean the same thing and both must behave the same way:
    # extraction returning `{"batch_no": null}` and extraction omitting the key.
    # A model does both depending on the document, and a Check has to fail
    # either way — mutation testing found this branch untested.
    omitted = [{"line_items": [{"hts_number": "3004.90"}]}]
    (row,) = resolve("commercial_invoice", omitted, "line_items[].batch_no")
    assert row.value is None
    assert row.locator == "commercial_invoice.line_items[0].batch_no"
