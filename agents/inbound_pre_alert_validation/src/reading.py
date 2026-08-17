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
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from meridian.runtime.ingest import UNRECOGNISED, Candidate, Source, Verdict

MODEL = "gpt-4o-mini"
"""Chosen for classification and field extraction over a page of text, not for
reasoning. Recorded in build.json; a sweep that fails on extraction quality is a
reason to raise this, and that is a tuning decision with an oracle."""

VISION_MODEL = "gpt-4o"
"""The fallback for a page with no text layer, which needs to see the page."""

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
    """
    phrases = {c.entity: keywords_of(c.identified_by) for c in candidates}
    groups: list[list[int]] = []
    for index, page in enumerate(pages):
        head = header_of(page)
        opens = any(phrase in head for words in phrases.values() for phrase in words)
        if opens or not groups:
            groups.append([index])
        else:
            groups[-1].append(index)

    return [
        Document(
            source=source,
            text="\n".join(pages[i] for i in group).strip(),
            pages=tuple(i + 1 for i in group),
        )
        for group in groups
    ]


class Classifier:
    """Which entity a document is, decided by a model over a closed set.

    Classify then dispatch: the model returns one key from the candidates it was
    handed plus a confidence, and every branch after that is code. An agentic
    loop picking its own next move would make the same eval case take different
    paths on different runs, which costs the suite its meaning.
    """

    def __init__(self, client: Any, model: str = MODEL) -> None:
        """Hold the model client this environment wants used."""
        self._client = client
        self._model = model

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
                        "Identify which kind of document this is. Reply as JSON: "
                        '{"entity": <one key or "unrecognised">, "confidence": 0.0-1.0}. '
                        "Answer unrecognised when it matches none of them. A certificate "
                        "of compliance is not a certificate of analysis."
                    ),
                },
                {"role": "user", "content": f"Kinds:\n{options}\n\nDocument:\n{text[:6000]}"},
            ],
        )
        return _verdict(answer.choices[0].message.content or "{}")


class Extractor:
    """The fields a document carries, against the schema the spec declares.

    The schema is handed over verbatim from `spec.lock.json`, so adding a field
    to an entity changes what is extracted with no change here.
    """

    def __init__(self, client: Any, model: str = MODEL) -> None:
        """Hold the model client this environment wants used."""
        self._client = client
        self._model = model

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
                        "Extract every instance of this document type from the text. "
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


def read_pages(data: bytes, client: Any, pages: Pages = pdf_pages) -> Sequence[str]:
    """Page text, falling back to the model for pages with no text layer.

    The fallback is per page rather than per file because the corpus is mixed:
    sending a whole native document to a vision model would be slow and worse,
    and refusing the one scanned bundle would lose a whole shipment.
    """
    text = list(pages(data))
    if any(len(page.strip()) >= MIN_TEXT_CHARACTERS for page in text):
        return text
    return [_seen(data, client)]


def _seen(data: bytes, client: Any) -> str:
    """What a model reads off a document that carries no text at all."""
    answer = client.chat.completions.create(
        model=VISION_MODEL,
        temperature=0,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Transcribe this document, preserving layout."},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:application/pdf;base64,{base64.b64encode(data).decode()}"
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
