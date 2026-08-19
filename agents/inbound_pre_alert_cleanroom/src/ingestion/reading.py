"""A PDF, page by page, as text or as an image.

The corpus is mixed and that is the whole reason this module has two modes.
Invoices and packing lists are native PDFs with a text layer, which is fast,
free and exact. Certificates of analysis are scans — every page yields zero
characters — so they have to be looked at.

Text first, vision as the fallback, decided per page rather than per file: a
bundle can be half one and half the other, and paying for vision on a page whose
text is already there is the sort of cost that only shows up on the invoice.

**Text is read in layout order, not in stream order.** These invoices are forms:
a label sits in one cell and its value in the next, and the PDF's own content
stream emits every label first and then every value, so ``CONTAINER NO.`` ends
up a dozen lines from the container. Sorting by position puts them back on the
same line, which is the difference between an extractor reading a field and an
extractor guessing which of thirty loose values belongs to it.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

# A page yielding fewer characters than this has no usable text layer. Set well
# above zero because a scanned page often carries a stamp or a footer that the
# extractor picks up while the body of the page stays an image.
TEXT_FLOOR = 120

# Rendering resolution for the vision fallback. 150 dpi keeps a batch number on
# a certificate legible without making every page a megabyte.
RENDER_DPI = 150


class UnreadableError(Exception):
    """The bytes that arrived are not something this reader can open.

    Raised rather than returning nothing: an empty document and an unopenable
    one produce the same absent entity, and only one of them is a bad
    recognition rule.
    """


@dataclass(frozen=True)
class Page:
    """One page of one attachment."""

    number: int
    text: str

    def has_text(self) -> bool:
        """Whether this page can be read without looking at it."""
        return len(self.text.strip()) >= TEXT_FLOOR


def pages_of(content: bytes) -> tuple[Page, ...]:
    """Every page's text layer, in order."""
    try:
        document = pymupdf.open(stream=content, filetype="pdf")  # type: ignore[no-untyped-call]
    except Exception as error:  # pymupdf raises several unrelated types
        raise UnreadableError(f"not a readable PDF: {error}") from error
    with document:
        return tuple(
            Page(number=n, text=str(document[n].get_text(sort=True)))  # type: ignore[no-untyped-call]
            for n in range(document.page_count)
        )


def images_of(content: bytes, numbers: range | tuple[int, ...]) -> tuple[bytes, ...]:
    """Selected pages rendered to PNG, for the pages no text layer covers."""
    try:
        document = pymupdf.open(stream=content, filetype="pdf")  # type: ignore[no-untyped-call]
    except Exception as error:
        raise UnreadableError(f"not a readable PDF: {error}") from error
    with document:
        return tuple(
            bytes(document[n].get_pixmap(dpi=RENDER_DPI).tobytes("png"))  # type: ignore[no-untyped-call]
            for n in numbers
            if 0 <= n < document.page_count
        )
