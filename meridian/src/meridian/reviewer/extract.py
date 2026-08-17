"""Getting the words off a document, whatever the document is.

A written procedure arrives as whatever the customer has. Sometimes that is
markdown in a repo; more often it is a PDF somebody exported from Word, and
sometimes it is a photograph of a laminated sheet taped to a wall in a
warehouse. All three have to reach the reviewer as text, and only one of them
already is.

**No OCR library.** The model that reads the procedure can read the page, so a
scan goes to it as a file and comes back as text. That is one dependency instead
of Tesseract plus a PDF layer plus the glue, it handles a photograph taken at an
angle, and it keeps the "one seam to OpenAI" property that `core.llm` exists to
protect — same model choice, same pinned temperature, same fake transport in
tests.

**A file that is already text is never sent.** Markdown is read off disk. Paying
a model to retype a file you can open is the kind of cost that looks like
sophistication and is waste.

What comes back is deliberately just text. Structuring it is the next stage's job
(`reference.read`), which aligns each statement onto the element of the board it
bears on — and that stage should not have to care whether the words arrived from
a file or a photograph.
"""

import asyncio
import base64
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from meridian.core.llm import Task, Transport, structured

# Read off disk. Anything whose bytes are already the words.
PLAIN = frozenset({".md", ".txt", ".markdown", ".rst", ".text"})

# Sent to the model as a file it can page through.
DOCUMENTS: dict[str, str] = {".pdf": "application/pdf"}

# Sent as an image, which is the case an OCR library would handle worst: a
# photograph of a printed sheet, at an angle, in warehouse lighting.
IMAGES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

SYSTEM = """\
You are reading a document so that somebody else can work from its text.

Transcribe what is on the page, in reading order. Keep the numbering and the
headings the author used, because a procedure's structure is part of what it
says — step 4 depending on step 3 is information.

Three things to get right:

  · Transcribe, do not summarise. A clause you compress is a rule somebody
    loses. Length is not a problem here.
  · Where the page is genuinely unreadable — a torn corner, a word under a
    coffee stain — write [unreadable] rather than guessing. A plausible
    invention is worse than a gap, because a gap can be noticed.
  · Ignore furniture: page numbers, headers and footers repeated on every page,
    watermarks. Keep tables, and lay them out so the rows still read as rows.

Return the text and nothing else — no preamble, no commentary on the document.\
"""


class Transcribed(BaseModel):
    """The words on the page."""

    text: str = Field(description="the document's text, in reading order")


class UnreadableError(ValueError):
    """A file this cannot turn into text.

    Carries the suffix rather than a generic message because the fix depends on
    it: an unknown extension is usually the wrong file, and a `.docx` is a
    conversion away from working.
    """

    def __init__(self, path: Path) -> None:
        """Name the file and what would make it readable."""
        self.path = path
        super().__init__(
            f"cannot read {path.name}: {path.suffix or 'no extension'} is not text, "
            f"a PDF, or an image. Export it as PDF or markdown first."
        )


async def text_of(path: Path, *, transport: Transport | None = None) -> str:
    """The document's text, read off disk or off the page.

    Args:
        path: the file. Markdown and plain text are read directly; PDFs and
            images go to the model.
        transport: the function that talks to OpenAI. Tests pass a fake.

    Returns:
        The document's text, in reading order.

    Raises:
        UnreadableError: the file is neither text, a PDF, nor an image.
    """
    suffix = path.suffix.lower()
    # Off the loop, because the API calls this too and a scanned procedure is
    # megabytes rather than kilobytes.
    if suffix in PLAIN:
        return await asyncio.to_thread(path.read_text)

    part = await _as_content(path, suffix)
    if part is None:
        raise UnreadableError(path)

    read = await structured(
        Task.EXTRACT,
        Transcribed,
        SYSTEM,
        [{"type": "input_text", "text": "Transcribe this document."}, part],
        transport=transport,
    )
    return read.value.text


async def _as_content(path: Path, suffix: str) -> dict[str, Any] | None:
    """The file as one content part, or ``None`` if this cannot carry it.

    A data URI rather than an upload: the file is read once, sent once, and never
    stored anywhere this system has to manage or clean up. At the size of a
    written procedure that is the right trade — a four-page SOP is well inside
    what one request carries.
    """
    raw = await asyncio.to_thread(path.read_bytes)
    encoded = base64.b64encode(raw).decode()
    if media := DOCUMENTS.get(suffix):
        return {
            "type": "input_file",
            "filename": path.name,
            "file_data": f"data:{media};base64,{encoded}",
        }
    if media := IMAGES.get(suffix):
        return {"type": "input_image", "image_url": f"data:{media};base64,{encoded}"}
    return None


def readable(suffix: str) -> bool:
    """Whether a file with this extension can be turned into text."""
    lowered = suffix.lower()
    return lowered in PLAIN or lowered in DOCUMENTS or lowered in IMAGES


__all__ = ["DOCUMENTS", "IMAGES", "PLAIN", "Transcribed", "UnreadableError", "readable", "text_of"]
