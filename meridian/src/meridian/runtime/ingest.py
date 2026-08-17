"""Attachments in, entity instances out.

Nothing here knows what an invoice is. It knows there are N candidate entity
types, each carrying a recognition rule the process owner wrote, and that is the
whole of its knowledge — which is why adding an entity to a board adds no code.

**Classify then dispatch, never tool-choice.** The model picks one key from a
closed set and the dispatch is a ``match``; an agentic loop choosing its own
tools would make control flow nondeterministic and the eval suite meaningless.

**One source may hold several instances, of different types.** A single
attachment in the running corpus is a certificate of compliance *and* a
certificate of analysis, and another is a 37-page bundle of an invoice and its
certificates. So a source yields a *list*, and a one-document file is a list of
length one — which means the question "is this one PDF or twelve" never has to
be answered.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from meridian.runtime.entities import EntityStore

UNRECOGNISED = "unrecognised"


@dataclass(frozen=True)
class Source:
    """Something that arrived and might be one of the entities we expect."""

    ref: str
    """How to fetch it. A URL, a storage path — the reader's business, not ours."""
    name: str = ""
    media_type: str = ""


@dataclass(frozen=True)
class Candidate:
    """One entity type a source might turn out to be."""

    entity: str
    identified_by: str
    """The recognition rule, in the process owner's own words.

    *"The page header reads 'Commercial Invoice'"* — which is page-level, not
    file-level, and is exactly why one file can yield several instances.
    """
    fields: Mapping[str, Any]
    """JSON Schema, straight off the spec. The extractor's only instruction."""


class Reader(Protocol):
    """Turns a source into something a classifier and extractor can read."""

    def read(self, source: Source) -> str:
        """Text for this source, however it has to be obtained."""
        ...


@dataclass(frozen=True)
class Verdict:
    """What a classifier decided, and how sure it was.

    Confidence is not decoration. A classifier forced to pick the nearest class
    for every input has no way to say "this is a certificate of compliance, not
    of analysis, and I am guessing" — and the running corpus contains a single
    file holding both. The industry checklist for these pipelines asks for an
    ``other`` class **and** a confidence threshold **and** a review path, for
    exactly that reason.

    Low confidence routes to a human rather than to a wrong extraction schema,
    which is the ``NeedsHumanError`` branch of the error taxonomy finally having
    something that reaches it.
    """

    entity: str
    confidence: float = 1.0


Classifier = Callable[[str, Sequence[Candidate]], Verdict]
"""Text plus the closed set of candidates, in — a verdict out."""

Extractor = Callable[[str, Candidate], Sequence[Mapping[str, Any]]]
"""Text plus one candidate, in — every instance of it found there, out."""


@dataclass(frozen=True)
class Pipeline:
    """How this environment turns a source into instances.

    Bundled rather than passed separately because they are one decision, not
    three: production reads over the network and calls a model twice, the eval
    harness reads fixtures and matches headers, and swapping between them is
    swapping *the pipeline* — which is also what keeps the suite an oracle.
    """

    read: Reader
    classify: Classifier
    extract: Extractor


def ingest(
    sources: Sequence[Source],
    candidates: Sequence[Candidate],
    pipeline: Pipeline,
    store: EntityStore | None = None,
    confidence_floor: float = 0.6,
) -> EntityStore:
    """Fill a store from whatever arrived.

    A source that cannot be read is declined rather than raised on. One
    unreadable scan must not stop a shipment whose other nine documents are
    fine; the Check that needs the missing entity is the thing entitled to
    complain, and it will.
    """
    store = store or EntityStore()

    for source in sources:
        try:
            text = pipeline.read.read(source)
        except Exception as error:
            store.decline(source.name or source.ref, f"could not be read: {error}")
            continue

        verdict = pipeline.classify(text, candidates)
        entity = verdict.entity

        if entity != UNRECOGNISED and verdict.confidence < confidence_floor:
            # Recognised, but not confidently. Extracting against the wrong
            # schema produces fields that look right and are not, which is far
            # worse than declining — and a certificate of compliance reads
            # almost exactly like a certificate of analysis.
            store.decline(
                source.name or source.ref,
                f"looks like {entity} but only {verdict.confidence:.0%} confident",
            )
            continue

        if entity == UNRECOGNISED:
            # The SOP says *locate* the invoice among the attachments, so
            # something matching nothing is not part of this process. Recorded
            # anyway: "found no invoices" and "skipped the invoice" are the same
            # empty result with entirely different fixes.
            store.decline(source.name or source.ref, "matches no recognition rule")
            continue

        found = next((c for c in candidates if c.entity == entity), None)
        if found is None:
            # The classifier answered outside the closed set it was given. That
            # is a bug in the classifier, not a document the process should
            # silently drop.
            store.decline(source.name or source.ref, f"classified as unknown type {entity!r}")
            continue

        for instance in pipeline.extract(text, found):
            store.add(found.entity, instance)

    return store


def candidates_from(entities: Mapping[str, Mapping[str, Any]]) -> tuple[Candidate, ...]:
    """The candidate set for a spec, skipping entities nothing can recognise.

    An entity with no ``identified_by`` is never something that *arrives* — it
    is returned by a lookup, or accumulated by checks — so offering it to a
    classifier would invite a match that cannot be right.
    """
    return tuple(
        Candidate(
            entity=key,
            identified_by=str(config["identified_by"]),
            fields=config.get("fields") or {},
        )
        for key, config in entities.items()
        if config.get("identified_by")
    )
