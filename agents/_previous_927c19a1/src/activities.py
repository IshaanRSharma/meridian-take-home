"""The activity boundary for this process: everything that touches the world.

Two activities reach the worker. `Capabilities.invoke` is the scaffold's, shared
by every agent, and handles the Actions. `Ingestion.read_documents` is this
one's, and it exists because turning attachments into entity instances is a
network call and two model calls — none of which may happen in workflow code,
where Temporal replays from history and a second answer breaks recovery
silently.

The split inside is deliberate: the activity fetches and reads, and hands back
plain data. Every decision made about that data — which check runs, which
outcome, which branch — happens above the boundary where it is replayable.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import mail
import spec
from reading import Classifier, Document, Extractor, read_pages, readable, split
from temporalio import activity

from meridian.runtime.ingest import Pipeline, candidates_from, ingest


@dataclass
class Attachment:
    """One file to fetch, named by where it came from.

    Carried on the signal rather than looked up again, so the workflow's history
    records exactly which files it was told about.
    """

    message_id: str = ""
    attachment_id: str = ""
    filename: str = ""
    media_type: str = ""


@dataclass
class Arrival:
    """One pre-alert email, as the trigger saw it.

    A dataclass because Temporal's converter refuses bare `object`, and because
    a signal outlives the signature it was first given — adding a field here
    does not break an instance already waiting.
    """

    message_id: str = ""
    subject: str = ""
    sender: str = ""
    received_at: str = ""
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class Gathered:
    """What ingestion found, as JSON across the activity boundary.

    Serialised rather than typed because the shape is the spec's, not this
    module's: entity keys and their fields come from `spec.lock.json`, and a
    dataclass here would have to be regenerated every time a board grows a
    field.
    """

    instances: str = "{}"
    declined: str = "[]"
    provenance: str = "{}"
    """How many instances of each entity were read, and how many survived.

    Counts alone cannot say whether two invoices are two documents or one
    document read twice, and those have opposite fixes. Read-versus-kept
    separates them in the trace, where a bundle can print it, instead of
    requiring somebody to re-run ingestion by hand to find out."""

    def entities(self) -> dict[str, list[dict[str, Any]]]:
        """The instances, back as data."""
        loaded: dict[str, list[dict[str, Any]]] = json.loads(self.instances)
        return loaded

    def read_and_kept(self) -> dict[str, dict[str, int]]:
        """Per entity, how many instances were read and how many were distinct."""
        loaded: dict[str, dict[str, int]] = json.loads(self.provenance)
        return loaded

    def skipped(self) -> list[tuple[str, str]]:
        """What arrived and was not part of this process, with the reason."""
        return [(str(a), str(b)) for a, b in json.loads(self.declined)]


class Ingestion:
    """Attachments to entity instances, over the live mailbox.

    A class because the activity needs clients injected at worker construction —
    the standard shape for a dependency in the SDK, and what lets the eval
    harness register the same activity over a different pipeline.
    """

    def __init__(self, gmail: mail.Gmail, model_client: Any) -> None:
        """Hold the mailbox and the model this environment wants used."""
        self._gmail = gmail
        self._classify = Classifier(model_client)
        self._extract = Extractor(model_client)
        self._model = model_client

    @activity.defn(name="read_documents")
    async def read_documents(self, arrivals: Sequence[Arrival]) -> Gathered:
        """Fetch every attachment on every arrival and read what is in it.

        The email itself is stored as an instance too. It is captured by the
        event and read by no check, which is not a reason to drop it: it carries
        the sender a report would reply to and the attachment list that says
        what was supposed to be here.
        """
        candidates = candidates_from(spec.entities())
        documents: list[Document] = []
        store = None
        declined: list[tuple[str, str]] = []

        for arrival in arrivals:
            for attachment in arrival.attachments:
                if not readable(attachment.filename, attachment.media_type):
                    declined.append((attachment.filename, "not a format this build reads"))
                    continue
                try:
                    data = self._gmail.download(mail.Attachment(**vars(attachment)))
                    pages = read_pages(data, self._model)
                except Exception as error:
                    declined.append((attachment.filename, f"could not be fetched: {error}"))
                    continue
                documents.extend(split(attachment.filename, pages, candidates))

        text = {document.as_source().ref: document.text for document in documents}
        store = ingest(
            [document.as_source() for document in documents],
            candidates,
            Pipeline(read=_Cached(text), classify=self._classify, extract=self._extract),
        )

        for arrival in arrivals:
            store.add(
                "prealert_email",
                {
                    "sender": arrival.sender,
                    "subject": arrival.subject,
                    "received_at": arrival.received_at,
                    "attachment_names": [a.filename for a in arrival.attachments],
                },
            )

        declined.extend((s.source, s.reason) for s in store.skipped)
        kept = {key: _distinct(store.instances(key)) for key in store.counts()}
        return Gathered(
            instances=json.dumps(kept),
            declined=json.dumps(declined),
            provenance=json.dumps(
                {
                    key: {"read": len(store.instances(key)), "kept": len(kept[key])}
                    for key in store.counts()
                }
            ),
        )


def _distinct(instances: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One entry per distinct document, however many times it arrived.

    A shipment is several emails and the later ones repeat the earlier ones'
    attachments — a corrected certificate comes with the whole bundle again, not
    on its own. Counting the same invoice once per email makes `invoices_total`
    the number of emails rather than the number of invoices, and every count
    derived from it wrong by the same factor.

    **Nothing on the board declares an identity for an entity.** `identified_by`
    says how to *recognise* one, which is a different question from how to tell
    two apart, so identity has to be inferred from what was read.

    Byte-equality is too strict. The same document read from two page ranges
    gives one full reading and one fragment, and they are not equal — so both
    survive, and a count that should be one is two. Agreement on the scalar
    fields both populate is the workable notion: two readings of one document
    agree on everything they both saw, while two genuinely different documents
    differ on the field that names them.

    Where two agree, they are **combined, not ranked**. Documents are split for
    reading before anything knows where one ends, so a document whose rows run
    past a page break is read as two, and each reading holds the rows on its own
    pages. Keeping the longer one discards the other's rows — which is invisible,
    because the count that drops is the count of things the check was supposed
    to examine, and a check that examines fewer rows reports fewer failures.
    """
    merged: dict[str, dict[str, Any]] = {}
    for instance in instances:
        key = _identity(instance)
        merged[key] = _combine(merged[key], instance) if key in merged else dict(instance)
    return list(merged.values())


def _combine(into: Mapping[str, Any], addition: Mapping[str, Any]) -> dict[str, Any]:
    """Two readings of one document, as everything either of them saw.

    Lists are appended, dropping rows already present, because a row appearing
    in both readings is one row seen twice and a row in only one is a row the
    other's pages did not cover. Scalars keep the first non-empty value: they
    agree by construction, since agreeing on them is what made these one
    document.
    """
    combined = dict(into)
    for name, value in addition.items():
        if isinstance(value, list):
            existing = combined.get(name)
            rows = list(existing) if isinstance(existing, list) else []
            seen = {json.dumps(row, sort_keys=True, default=str) for row in rows}
            for row in value:
                stamp = json.dumps(row, sort_keys=True, default=str)
                if stamp not in seen:
                    seen.add(stamp)
                    rows.append(row)
            combined[name] = rows
        elif not combined.get(name) and value is not None:
            combined[name] = value
    return combined


def _identity(instance: Mapping[str, Any]) -> str:
    """What two readings of the same document agree on.

    Scalars only. A list is where a partial reading differs — one page of line
    items against five — so including it would make every fragment its own
    document, which is the behaviour being fixed.
    """
    scalars = {
        field: value
        for field, value in sorted(instance.items())
        if value is not None and not isinstance(value, list | dict) and str(value).strip()
    }
    return json.dumps(scalars, sort_keys=True, default=str)




@dataclass(frozen=True)
class _Cached:
    """A reader over text that has already been pulled out of the bytes.

    Splitting has to happen before classification — one attachment can hold an
    invoice and a dozen certificates — so by the time `ingest` asks for a
    source's text it has been read once already. Reading it twice would double
    the model calls on the one document that has no text layer.
    """

    text: dict[str, str]

    def read(self, source: Any) -> str:
        """The text for one already-split document."""
        return self.text.get(source.ref, "")
