"""Which document a page is, and what it says.

Classify then dispatch, never tool-choice: the model picks one key from the
closed set the spec declares and the dispatch is code. An agentic loop choosing
its own next move would make control flow nondeterministic and the eval suite
meaningless.

**Classification is page-level.** ``identified_by`` says *"the page header
reads…"*, which describes a page and not a file. One attachment in the corpus is
a thirty-seven page bundle of an invoice and its certificates, and another is a
certificate of compliance and of analysis in one PDF, so a source yields a list
of segments and a single-document file is a list of length one.

**Filenames are never evidence.** ``MMAU1407799.pdf`` is named after a container
and holds a waybill; ``180-465.pdf`` is the invoice. Only the page decides.

Every seam here is injected rather than called directly, because each is a place
reality reliably differs from a description of it: how a source is read, how it
is classified, how sure is sure enough. A repair swaps a function; buried in a
loop, the same repair is a rewrite.
"""

from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import openai

from ingestion.reading import Page, UnreadableError, images_of, pages_of
from ingestion.schemas import INSTANCES, instances_schema
from meridian.runtime import Candidate

# Bumped whenever the prompts, the temperature or the schema treatment change.
# It is part of the cache key, because a cached answer produced under different
# instructions is a different answer wearing the same name -- which is the way a
# cache goes quietly wrong rather than loudly.
PROMPT_VERSION = "cleanroom-3"

# A provider that is rate limited, timing out or down is transient, and the
# mailbox is read in bursts of concurrent calls, so this is reached routinely
# rather than exceptionally. Giving up would turn a busy minute into an errored
# eval case, which is a measurement lost for no reason.
TRANSIENT = (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError)
MAX_ATTEMPTS = 6
BACKOFF_SECONDS = 2.0

# Extraction has to be reproducible: instances are deduplicated on their
# extracted content, because nothing on a board declares an identity for an
# entity, so the same document read twice has to produce the same bytes.
TEMPERATURE = 0.0

# Below this a document is declined rather than extracted. A certificate of
# compliance reads almost exactly like a certificate of analysis, and extracting
# against the wrong schema yields fields that look right and are not — which is
# worse than an absent entity, because nothing downstream can tell.
CONFIDENCE_FLOOR = 0.6

# How much of a page's text the classifier is shown. A header is at the top; a
# whole thirty-seven page bundle would be mostly line items and mostly noise.
CLASSIFY_CHARS = 1200

# How many scanned pages are rendered per render call. A batch size, not a
# budget: every scanned page is rendered, in chunks of this many, because a
# bundle's later pages are documents too.
#
# It was a cap, and the cap was silently losing documents. The certificates for
# a shipment arrive as one scanned bundle — 24 pages for HLBU6302759, 48 for
# MCAU6047165 — and rendering only the first 16 meant the classifier was asked
# which documents a file contained while never being shown two thirds of it. It
# answered for what it saw, so the shipment reported certificates missing for
# batches whose certificates were in the attachment, eight and thirty-two pages
# down. Nothing said so: the pages were dropped before the model, not declined.
VISION_PAGES = 16

Part = tuple[str, str]
"""One piece of a prompt: ``("text", …)`` or ``("image", <base64 png>)``."""


class Model(Protocol):
    """A structured-output call. Injected so nothing here holds a vendor."""

    def json(self, instructions: str, parts: Sequence[Part], schema: Mapping[str, Any]) -> str:
        """Answer with a JSON document conforming to ``schema``."""
        ...


@dataclass(frozen=True)
class Segment:
    """A run of pages that is one instance of one entity."""

    entity: str
    first_page: int
    last_page: int
    confidence: float


@dataclass(frozen=True)
class Recognised:
    """What one attachment turned out to hold."""

    instances: tuple[tuple[str, dict[str, Any]], ...] = ()
    declined: tuple[str, ...] = ()


class OpenAIModel:
    """The structured-output call, at temperature zero.

    Determinism is not a preference here: instances are deduplicated on their
    extracted content, because nothing on a board declares an identity for an
    entity, so the same document read twice has to produce the same bytes.
    """

    def __init__(self, client: Any, model: str) -> None:
        """Take an already-authenticated client and the model to call."""
        self._client = client
        self._model = model

    def json(self, instructions: str, parts: Sequence[Part], schema: Mapping[str, Any]) -> str:
        """One call, refused rather than repaired if the answer is not JSON."""
        content: list[dict[str, Any]] = [{"type": "input_text", "text": instructions}]
        for kind, value in parts:
            if kind == "image":
                content.append(
                    {"type": "input_image", "image_url": f"data:image/png;base64,{value}"}
                )
            else:
                content.append({"type": "input_text", "text": value})
        request = {
            "model": self._model,
            "temperature": TEMPERATURE,
            "input": [{"role": "user", "content": content}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "reading",
                    "schema": dict(schema),
                    "strict": True,
                }
            },
        }
        return str(self._attempt(request).output_text)

    def _attempt(self, request: Mapping[str, Any]) -> Any:
        """One call, retried while the failure is the provider's rather than ours.

        Exponential rather than fixed, because the whole mailbox is read at once
        and a fixed interval would send the same burst back into the same limit.
        """
        for attempt in range(MAX_ATTEMPTS):
            try:
                return self._client.responses.create(**request)
            except TRANSIENT:
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                time.sleep(BACKOFF_SECONDS * 2**attempt)
        raise RuntimeError("unreachable: the loop either returns or re-raises")


def openai_model() -> OpenAIModel:
    """The model this deployment reads documents with."""
    from openai import OpenAI  # noqa: PLC0415 - kept out of the workflow's import graph

    return OpenAIModel(
        OpenAI(api_key=os.environ["OPENAI_API_KEY"]),
        os.environ.get("OPENAI_MODEL", "gpt-5.4-mini"),
    )


def recognise(
    name: str,
    content: bytes,
    candidates: Sequence[Candidate],
    model: Model,
    floor: float = CONFIDENCE_FLOOR,
) -> Recognised:
    """Everything one attachment holds that this process declared.

    Args:
        name: what the attachment is called, for the record of what was skipped.
        content: the bytes that arrived.
        candidates: the closed set of entity types, with the owner's own
            recognition rule for each.
        model: how a page is classified and read.
        floor: how sure the classifier must be before a schema is applied.

    Returns:
        The instances found, and a reason for anything skipped.
    """
    try:
        pages = pages_of(content)
    except UnreadableError as error:
        return Recognised(declined=(f"{name}: {error}",))
    if not pages:
        return Recognised(declined=(f"{name}: carries no pages",))

    segments = _segments(pages, content, candidates, model)
    if not segments:
        # The SOP says *locate* the invoice among the attachments, so something
        # matching nothing is simply not part of this process — recorded, never
        # silent, because "found no invoices" and "skipped the invoice" are the
        # same empty result with entirely different fixes.
        return Recognised(declined=(f"{name}: matches no recognition rule",))

    found: list[tuple[str, dict[str, Any]]] = []
    declined: list[str] = []
    by_entity = {one.entity: one for one in candidates}
    for segment in segments:
        candidate = by_entity.get(segment.entity)
        if candidate is None:
            declined.append(f"{name} p{segment.first_page + 1}: unknown type {segment.entity!r}")
            continue
        if segment.confidence < floor:
            declined.append(
                f"{name} p{segment.first_page + 1}: looks like {segment.entity} "
                f"but only {segment.confidence:.0%} confident"
            )
            continue
        found.extend(
            (candidate.entity, values)
            for values in _extract(pages, content, segment, candidate, model)
        )
    return Recognised(instances=tuple(found), declined=tuple(declined))


def _segments(
    pages: Sequence[Page], content: bytes, candidates: Sequence[Candidate], model: Model
) -> tuple[Segment, ...]:
    parts = _parts(pages, content, range(len(pages)), CLASSIFY_CHARS)
    schema = {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "entity": {"type": "string", "enum": [c.entity for c in candidates]},
                        "first_page": {"type": "integer"},
                        "last_page": {"type": "integer"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["entity", "first_page", "last_page", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["segments"],
        "additionalProperties": False,
    }
    rules = "\n".join(f"- {c.entity}: {c.identified_by}" for c in candidates)
    instructions = (
        "The pages below are one email attachment, numbered from 1.\n"
        "Decide which of these document types each page belongs to, using only the "
        f"recognition rule given for each:\n{rules}\n\n"
        "Group consecutive pages of the same document into one segment, and report one "
        "segment per document instance. A page matching none of the rules belongs to no "
        "segment — leave it out rather than forcing it into the nearest type. "
        "confidence is how sure you are, between 0 and 1; be honest when two types read "
        "alike. Page numbers are 1-based and inclusive."
    )
    answered = json.loads(model.json(instructions, parts, schema))
    return tuple(
        Segment(
            entity=str(one["entity"]),
            first_page=max(0, int(one["first_page"]) - 1),
            last_page=max(0, int(one["last_page"]) - 1),
            confidence=float(one["confidence"]),
        )
        for one in answered.get("segments", [])
    )


def _extract(
    pages: Sequence[Page],
    content: bytes,
    segment: Segment,
    candidate: Candidate,
    model: Model,
) -> list[dict[str, Any]]:
    last = min(max(segment.last_page, segment.first_page), len(pages) - 1)
    # A classifier that answers with its range inverted would otherwise send the
    # extractor no pages at all, and an extraction with nothing to read comes
    # back empty rather than wrong -- which is the failure that looks like an
    # absent document instead of a bad answer.
    span = range(segment.first_page, last + 1)
    parts = _parts(pages, content, span, None)
    instructions = (
        f"Read this {candidate.entity} and return every instance of it on these pages.\n"
        f"{candidate.identified_by}\n\n"
        "Copy values exactly as printed — do not tidy spacing, case or punctuation, and do "
        "not infer a value that is not there. A field the document does not carry is null, "
        "and a line item missing one of its codes is still a line item: never drop a row for "
        "being incomplete."
    )
    answered = json.loads(model.json(instructions, parts, instances_schema(candidate.fields)))
    return [dict(one) for one in answered.get(INSTANCES, [])]


def _parts(
    pages: Sequence[Page], content: bytes, span: range, limit: int | None
) -> list[Part]:
    """Text for the pages that have it, rendered images for the pages that do not."""
    parts: list[Part] = []
    scanned: list[int] = []
    for number in span:
        page = pages[number]
        if page.has_text():
            body = page.text[:limit] if limit else page.text
            parts.append(("text", f"--- page {number + 1} ---\n{body}"))
        else:
            scanned.append(number)

    for start in range(0, len(scanned), VISION_PAGES):
        batch = tuple(scanned[start : start + VISION_PAGES])
        # strict: every page asked for comes back, or the count is wrong and a
        # document goes missing without anything saying so. That silence is what
        # made this cost a shipment's certificates rather than raising.
        for number, image in zip(batch, images_of(content, batch), strict=True):
            parts.append(("text", f"--- page {number + 1} (scanned) ---"))
            parts.append(("image", base64.b64encode(image).decode()))
    return parts
