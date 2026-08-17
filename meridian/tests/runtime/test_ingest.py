"""Attachments in, entity instances out — with the real inbox's shapes.

Every fixture here is something the running corpus actually contains: signature
gifs on every message, a house bill of lading nobody asked for, a spreadsheet, a
single PDF holding two document types, and a filename that names a container
rather than its contents.
"""

import json
import pathlib

import pytest

from meridian.runtime.entities import EntityStore
from meridian.runtime.ingest import (
    UNRECOGNISED,
    Candidate,
    Pipeline,
    Source,
    Verdict,
    candidates_from,
    ingest,
)

SPEC = json.loads(
    (pathlib.Path(__file__).resolve().parents[2] / "db/seeds/prealert_board.json").read_text()
)

INVOICE = Candidate(
    entity="commercial_invoice",
    identified_by='the page header reads "Commercial Invoice"',
    fields={"invoice_no": {"type": "string"}},
)
COA = Candidate(
    entity="certificate_of_analysis",
    identified_by='the page header reads "Certificate of Analysis"',
    fields={"batch_no": {"type": "string"}},
)

# Verbatim from the mailbox. Filenames say nothing about content — `CGMU5630052`
# is a container number, and `U07-5284` could be anything.
ARRIVED = [
    Source(ref="1", name="image001.gif", media_type="image/gif"),
    Source(ref="2", name="SH00029867 HBL.pdf", media_type="application/pdf"),
    Source(ref="3", name="U07-5284.pdf", media_type="application/pdf"),
    Source(ref="4", name="APL USA - SEA.xls", media_type="application/vnd.ms-excel"),
    Source(ref="5", name="SM8726049A COC&COA.PDF", media_type="application/pdf"),
]

PAGES = {
    "1": "",
    "2": "HOUSE BILL OF LADING\nShipper: APL",
    "3": "Commercial Invoice\nInvoice No: U07-5284\nDescription of Goods",
    "4": "Packing list",
    "5": "Certificate of Compliance\n…\nCertificate of Analysis\nBatch No: SM8726049A",
}


class Pages:
    def read(self, source: Source) -> str:
        return PAGES[source.ref]


def classify(text: str, candidates) -> Verdict:
    """Stands in for the model. Same contract: one key from a closed set."""
    for candidate in candidates:
        header = candidate.identified_by.split('"')[1]
        if header.lower() in text.lower():
            return Verdict(candidate.entity)
    return Verdict(UNRECOGNISED)


def extract(text: str, candidate: Candidate):
    """One instance per occurrence of the header, as a page-level rule implies."""
    header = candidate.identified_by.split('"')[1]
    return [{"seen": header} for _ in range(text.lower().count(header.lower()))]


def _run(sources=ARRIVED, candidates=(INVOICE, COA)) -> EntityStore:
    return ingest(sources, candidates, Pipeline(Pages(), classify, extract))


def test_the_right_document_is_found_among_the_wrong_ones():
    # The whole question: seven attachments arrive and one is the invoice.
    assert len(_run().instances("commercial_invoice")) == 1


def test_everything_else_is_skipped_rather_than_failing():
    # The SOP says *locate* the invoice among the attachments, so an HBL, a
    # packing list and a signature gif are simply not part of this process.
    skipped = {s.source for s in _run().skipped}
    assert skipped == {"image001.gif", "SH00029867 HBL.pdf", "APL USA - SEA.xls"}


def test_skipping_is_never_silent():
    # "Found no invoices" and "skipped the invoice" are the same empty result
    # with entirely different fixes. Only one of them is a bad recognition rule.
    store = _run(sources=[Source(ref="2", name="SH00029867 HBL.pdf")])
    assert store.instances("commercial_invoice") == ()
    assert store.skipped[0].reason == "matches no recognition rule"


def test_one_file_can_hold_more_than_one_document():
    # `SM8726049A COC&COA.PDF` really is a certificate of compliance AND of
    # analysis. Recognition is page-level, so a source yields a list.
    assert len(_run().instances("certificate_of_analysis")) == 1


def test_a_filename_is_never_the_evidence():
    # `U07-5284.pdf` is the invoice and `CGMU5630052.pdf` is named after a
    # container. Classification reads the page, which is what the process owner
    # said to do.
    named_nothing = [Source(ref="3", name="CGMU5630052.pdf")]
    assert len(_run(sources=named_nothing).instances("commercial_invoice")) == 1


def test_an_unreadable_source_does_not_stop_the_others():
    class Broken:
        def read(self, source: Source) -> str:
            if source.ref == "2":
                raise OSError("scan is corrupt")
            return PAGES[source.ref]

    store = ingest(ARRIVED, (INVOICE, COA), Pipeline(Broken(), classify, extract))
    assert len(store.instances("commercial_invoice")) == 1
    assert any("could not be read" in s.reason for s in store.skipped)


def test_a_classifier_answering_outside_its_closed_set_is_caught():
    # An agentic loop picking its own answer is exactly what classify-then-
    # dispatch exists to prevent. If it happens anyway, it must not look like a
    # document the process chose to drop.
    store = ingest(
        [ARRIVED[2]], (INVOICE,), Pipeline(Pages(), lambda *_: Verdict("packing_list"), extract)
    )
    assert "unknown type" in store.skipped[0].reason


def test_candidates_come_from_the_spec_and_exclude_what_cannot_arrive():
    # An entity with no recognition rule is produced by a lookup or filled by a
    # check. Offering it to a classifier would invite a match that cannot be
    # right.
    entities = {
        p["key"]: p["config"] for p in SPEC["primitives"] if p["primitive_type"] == "entity"
    }
    entities["shipment_summary"] = {"fields": {"total": {"type": "integer"}}}

    keys = {c.entity for c in candidates_from(entities)}
    assert "commercial_invoice" in keys
    assert "shipment_summary" not in keys


def test_counts_are_the_first_line_of_any_extraction_failure():
    assert _run().counts() == {"certificate_of_analysis": 1, "commercial_invoice": 1}


@pytest.mark.parametrize("entity", ["commercial_invoice", "certificate_of_analysis"])
def test_an_entity_that_never_arrived_reads_empty_rather_than_raising(entity: str):
    # A certificate that never turned up is a finding the process reports, not
    # a crash. The Check is what complains.
    assert EntityStore().instances(entity) == ()


def test_a_guess_is_declined_rather_than_extracted_against_the_wrong_schema():
    # A certificate of compliance reads almost exactly like a certificate of
    # analysis, and the corpus has one file holding both. Extracting against the
    # wrong schema produces fields that look right and are not.
    unsure = Pipeline(Pages(), lambda *_: Verdict("commercial_invoice", 0.3), extract)
    store = ingest([ARRIVED[2]], (INVOICE,), unsure)

    assert store.instances("commercial_invoice") == ()
    assert "30% confident" in store.skipped[0].reason


def test_a_confident_verdict_is_extracted():
    sure = Pipeline(Pages(), lambda *_: Verdict("commercial_invoice", 0.95), extract)
    assert len(ingest([ARRIVED[2]], (INVOICE,), sure).instances("commercial_invoice")) == 1
