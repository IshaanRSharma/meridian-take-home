"""Turning attachment bytes into text, and text into entity instances.

Three seams, because these are the three places reality reliably differs from
any description of it, and each is passed in rather than called directly so a
repair swaps one function instead of unpicking a loop:

    split      bytes -> documents.  Where one document ends and the next begins.
    classify   text  -> which entity this is, and how sure
    extract    text  -> the instances on it

**Boundaries come from the spec, not from a guess.** Every `identified_by` on
this board is page-level — *"the page header reads 'Commercial Invoice'"* — so a
page whose head matches a recognition rule starts a new document and a page that
matches none continues the one before it. One attachment in the corpus is a
37-page bundle of an invoice and its certificates, and another is a single
certificate; the same rule handles both, and neither has to declare which it is.

**Text first, model second.** Reading a text layer is free and handles most of
the corpus. Only a page that yields nothing is worth sending to a model, and one
bundle in the corpus is pure image with no text layer at all.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from meridian.runtime.ingest import UNRECOGNISED, Candidate, Source, Verdict

MODEL = os.environ.get("AGENT_MODEL", "gpt-5.4-mini")
"""Classification and field extraction over a page of text.

Read from the environment so the model is a variable the sweep can vary rather
than a constant somebody has to edit. `agent_builds.model` already records what
a build ran on, which makes "did the score move because of the patch or because
of the model" a question the curve can answer instead of an argument.

Raised off `gpt-4o-mini`, which was failing `hts_number`, `fda_product_code`,
`ndc_number` and `anda_number` on every line item of every case — dense
structured identifiers in long documents is exactly where the mini tier gives
out. Extraction quality has an oracle, so this is a tuning decision, not a
business one."""

VISION_MODEL = os.environ.get("AGENT_VISION_MODEL", "gpt-5.4-mini")
"""The fallback for a page with no text layer, which needs to see the page."""

MAX_VISION_PAGES = int(os.environ.get("AGENT_MAX_VISION_PAGES", "60"))
"""How many pages of one scanned document to render.

Raised from 12, which was silently cutting certificates off the end of every
scanned bundle in the corpus. `29,30,31-Aurohealth LLC-Doc.pdf` is one file
holding three shipments' paperwork: pages 4-11 are the first batch's
certificate, page 12 begins the second, and the third was never rendered — so
the check reported a missing certificate for a batch whose certificate was in
the attachment all along. Same file shape, same truncation, in all three failing
cases.

A cap still exists because a runaway render is a real cost, but it belongs above
the largest document anybody sends rather than below the median bundle."""

MIN_TEXT_CHARACTERS = 40
"""Below this a page is treated as having no usable text layer. A header and a
page number clear 40 characters; a genuinely blank extraction does not."""


@dataclass(frozen=True)
class Document:
    """One document found inside one attachment, as text.

    `pages` is kept because a failure that names a page is diagnosable and one
    that names a file is an errand — a 37-page bundle has 37 places to look.
    """

    source: str
    text: str
    pages: tuple[int, ...]

    def as_source(self) -> Source:
        """The ingest-layer view of this document."""
        span = f"p{self.pages[0]}" if len(self.pages) == 1 else f"p{self.pages[0]}-{self.pages[-1]}"
        return Source(ref=f"{self.source}#{span}", name=f"{self.source} {span}")


class Pages(Protocol):
    """Bytes to one string per page."""

    def __call__(self, data: bytes) -> Sequence[str]:
        """Text for each page, in order, empty where there is no text layer."""
        ...


def pdf_pages(data: bytes) -> Sequence[str]:
    """Every page's text layer, in order.

    A page with no text layer yields an empty string rather than raising, so a
    mixed document — some pages native, some scanned — is still readable for the
    pages that are.
    """
    from pypdf import PdfReader  # noqa: PLC0415 - kept beside the one function that parses a PDF

    reader = PdfReader(io.BytesIO(data))
    out: list[str] = []
    for page in reader.pages:
        try:
            out.append(page.extract_text() or "")
        except Exception:
            out.append("")
    return out


def header_of(page: str, lines: int = 6) -> str:
    """The top of a page, which is where a recognition rule looks."""
    return " ".join(page.strip().splitlines()[:lines]).lower()


def keywords_of(identified_by: str) -> tuple[str, ...]:
    """The quoted phrases a recognition rule names.

    `identified_by` is prose written by a process owner, and the part that can
    be matched is what they put in quotes. Taking only the quoted phrases keeps
    the surrounding sentence — *"the page header reads"* — from being treated as
    something to look for.
    """
    quoted = re.findall(r"[\"'“‘]([^\"'”’]+)[\"'”’]", identified_by)  # noqa: RUF001 - a process owner types curly quotes
    return tuple(phrase.strip().lower() for phrase in quoted if phrase.strip())


def split(source: str, pages: Sequence[str], candidates: Sequence[Candidate]) -> list[Document]:
    """Group consecutive pages into documents, using the boards own headers.

    A page whose header carries a recognition phrase opens a document; anything
    after it belongs to that document until the next header appears. Pages
    before any recognisable header become a document of their own so they are
    classified and declined on the record rather than dropped in silence.

    **A repeated header is a continuation, not a second document.** The phrase
    lives in a letterhead, and a letterhead reprints on every page of the
    document it belongs to — so `Commercial Invoice` appears on page 2 of an
    invoice exactly as it does on page 1, and splitting there hands the reader a
    fragment with no goods on it. The model does not return nothing for such a
    fragment; it fills the schema from whatever is on the page, which on an
    invoice's second page is the consignee address block. That invented row then
    merges into the real invoice and inflates every count the check reports.

    An *identical* header is what separates the two cases, and it is the whole
    of the rule. Two real certificates in one bundle differ in the lines naming
    their batch, so they still open; page 2 of one certificate reprints page 1
    byte for byte, so it does not. Measured over this corpus: 16 transitions
    where a page repeats the previous page's header verbatim, and all 16 are
    continuations — none of them starts a new document.
    """
    phrases = {c.entity: keywords_of(c.identified_by) for c in candidates}
    groups: list[list[int]] = []
    previous = ""
    for index, page in enumerate(pages):
        head = header_of(page)
        carries = any(phrase in head for words in phrases.values() for phrase in words)
        opens = carries and head != previous
        if opens or not groups:
            groups.append([index])
        else:
            groups[-1].append(index)
        previous = head

    return [
        Document(
            source=source,
            text="\n".join(pages[i] for i in group).strip(),
            pages=tuple(i + 1 for i in group),
        )
        for group in groups
    ]


def learned(hints: Mapping[str, Sequence[str]], key: str) -> str:
    """What previous runs worked out about reading this kind of document.

    **This is the only part of the agent that changes without a code edit.**
    Everything else the healing loop can improve, it improves by writing Python,
    which needs a coding agent in the loop. Hints are a parameter: the trainer
    proposes one from the evidence in a failure, scores the suite with it, and
    keeps it only if the score went up — so the loop has something it can fit
    rather than only something it can rewrite.

    Kept outside the checksummed spec on purpose. A hint is an observation about
    what the documents look like, never a decision about what the business
    means, so it must not be able to drift the build from the contract.
    """
    said = [*hints.get("*", ()), *hints.get(key, ())]
    if not said:
        return ""
    lines = "\n".join(f"- {one}" for one in said)
    return f"Known about these documents, from previous runs:\n{lines}\n\n"


class Classifier:
    """Which entity a document is, decided by a model over a closed set.

    Classify then dispatch: the model returns one key from the candidates it was
    handed plus a confidence, and every branch after that is code. An agentic
    loop picking its own next move would make the same eval case take different
    paths on different runs, which costs the suite its meaning.
    """

    def __init__(
        self, client: Any, model: str = MODEL, hints: Mapping[str, Sequence[str]] | None = None
    ) -> None:
        """Hold the model client and whatever the loop has learned so far."""
        self._client = client
        self._model = model
        self._hints = hints or {}

    def __call__(self, text: str, candidates: Sequence[Candidate]) -> Verdict:
        """One verdict for one document."""
        if not text.strip():
            return Verdict(entity=UNRECOGNISED, confidence=1.0)

        options = "\n".join(f"- {c.entity}: {c.identified_by}" for c in candidates)
        answer = self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        learned(self._hints, "*")
                        +
                        "Identify which kind of document this is. Reply as JSON: "
                        '{"entity": <one key or "unrecognised">, "confidence": 0.0-1.0}. '
                        "Answer unrecognised when it matches none of them. A certificate "
                        "of compliance is not a certificate of analysis."
                    ),
                },
                {"role": "user", "content": f"Kinds:\n{options}\n\nDocument:\n{text[:6000]}"},
            ],
        )
        return corroborate(_verdict(answer.choices[0].message.content or "{}"), text, candidates)


def corroborate(verdict: Verdict, text: str, candidates: Sequence[Candidate]) -> Verdict:
    """Hold a verdict against the recognition rule the process owner wrote.

    `identified_by` says the page header reads a particular phrase. That is a
    checkable claim, and checking it costs nothing — so a document the model
    named without the header to support it is reported less confidently, and the
    existing floor decides what to do about it.

    This is not distrust of the model in general. It is that shipping paperwork
    all looks alike: a bill of lading sat in the corpus being read as a
    commercial invoice, which inflated the invoice count on every shipment
    carrying one. The header discriminates them exactly, because that is what
    the process owner said to look at.

    Confidence is halved rather than the verdict rejected, so there is still one
    knob — the floor — rather than two mechanisms disagreeing about the same
    question.
    """
    if verdict.entity == UNRECOGNISED:
        return verdict
    wanted = next((c.identified_by for c in candidates if c.entity == verdict.entity), "")
    phrases = keywords_of(wanted)
    if not phrases:
        # Nothing quoted to check against, so there is nothing to corroborate
        # and no reason to doubt what came back.
        return verdict
    head = header_of(text)
    if any(phrase in head for phrase in phrases):
        return verdict
    return Verdict(entity=verdict.entity, confidence=verdict.confidence / 2)


class Extractor:
    """The fields a document carries, against the schema the spec declares.

    The schema is handed over verbatim from `spec.lock.json`, so adding a field
    to an entity changes what is extracted with no change here.
    """

    def __init__(
        self, client: Any, model: str = MODEL, hints: Mapping[str, Sequence[str]] | None = None
    ) -> None:
        """Hold the model client and whatever the loop has learned so far."""
        self._client = client
        self._model = model
        self._hints = hints or {}

    def __call__(self, text: str, candidate: Candidate) -> Sequence[Mapping[str, Any]]:
        """Every instance of one entity found in one document.

        A list, always. Fields a check reads are nullable in the schema on
        purpose — a line item missing its batch number has to come back missing,
        or the check that exists to find that can never fire. Nothing is dropped
        here for being incomplete.
        """
        answer = self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        learned(self._hints, candidate.entity)
                        + "Extract every instance of this document type from the text. "
                        'Reply as JSON: {"instances": [ ... ]}, each matching the schema. '
                        "Use null for a field that is genuinely absent — never invent, "
                        "never omit a row because a field is missing, never normalise a "
                        "value beyond what is printed."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Schema for {candidate.entity}:\n"
                        f"{json.dumps(dict(candidate.fields), indent=1)}\n\n"
                        f"{candidate.identified_by}\n\nText:\n{text[:24000]}"
                    ),
                },
            ],
        )
        return _instances(answer.choices[0].message.content or "{}")


def readable(filename: str, media_type: str) -> bool:
    """Whether this build can read this file at all.

    A spreadsheet and a signature image are not documents this agent knows how
    to open, and handing them to a PDF parser produces `Stream has ended
    unexpectedly` — an error about parsing, from a file that was never going to
    be parsed, which reads like a bug in the reader. Deciding before opening
    keeps the decline honest: *not a format this build reads*, which is a
    coverage statement somebody can act on.
    """
    return filename.lower().endswith(".pdf") or media_type.lower() == "application/pdf"


def page_images(data: bytes, dpi: int = 150) -> list[bytes]:
    """Each page rendered to a PNG.

    Needed because a scanned page has to be *seen*. 150 dpi is the point where
    small print on a certificate stays legible without the payload growing
    faster than the accuracy does.
    """
    import pymupdf  # noqa: PLC0415 - kept beside the one function that rasterises

    # pymupdf ships no py.typed marker, so `open` reads as untyped here.
    with pymupdf.open(stream=data, filetype="pdf") as document:  # type: ignore[no-untyped-call]
        return [
            page.get_pixmap(dpi=dpi).tobytes("png")
            for page in document.pages(0, min(document.page_count, MAX_VISION_PAGES))
        ]


def read_pages(data: bytes, client: Any, pages: Pages = pdf_pages) -> tuple[Sequence[str], str]:
    """Page text, and which route produced it.

    The corpus is mixed: most documents are native PDF and free to read, and a
    few are pure image. Rendering every page of every file would be slow and no
    more accurate, so the fallback fires only when the text layer yields nothing
    at all.

    The route is returned because the cache needs it. Text-layer output is the
    same bytes whichever model is configured; a transcription is one model's
    output and must not outlive it.
    """
    text = list(pages(data))
    if any(len(page.strip()) >= MIN_TEXT_CHARACTERS for page in text):
        return text, "text"
    return [_seen(image, client) for image in page_images(data)], "vision"


def _seen(image: bytes, client: Any) -> str:
    """What a model reads off one rendered page.

    A page at a time, and as an **image**. A PDF handed to an image parameter is
    refused — *"Invalid MIME type. Only image types are supported."* — and the
    whole attachment is then declined for what reads like a fetch problem. Every
    scanned certificate in the corpus was lost that way.
    """
    answer = client.chat.completions.create(
        model=VISION_MODEL,
        temperature=0,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Transcribe this page, preserving layout."},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{base64.b64encode(image).decode()}"
                        },
                    },
                ],
            }
        ],
    )
    return answer.choices[0].message.content or ""


def _verdict(body: str) -> Verdict:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return Verdict(entity=UNRECOGNISED, confidence=1.0)
    entity = str(parsed.get("entity") or UNRECOGNISED)
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return Verdict(entity=entity, confidence=confidence)


def _instances(body: str) -> Sequence[Mapping[str, Any]]:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return ()
    found = parsed.get("instances")
    if isinstance(found, list):
        return [row for row in found if isinstance(row, dict)]
    return [parsed] if isinstance(parsed, dict) and parsed else []
